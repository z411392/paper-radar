import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime

from libs.discovery.domain.services.harvest_page_rules import HarvestPageRules
from libs.discovery.domain.services.prepare_harvest_capture import PrepareHarvestCapture
from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.harvest_page_result import HarvestPageResult
from libs.discovery.dtos.harvest_processing_snapshot import HarvestProcessingSnapshot
from libs.discovery.dtos.parsed_arxiv_page import ParsedArxivPage
from libs.discovery.exceptions.harvest_error import HarvestError


class SqliteHarvestProcessingAdapter:
    """One SQLite transaction owns observations, progress, and processing receipts."""

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            if connection.in_transaction:
                raise HarvestError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise HarvestError("foreign_keys_required")
            connection.row_factory = sqlite3.Row
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with connection:
                yield connection
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "harvest_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "harvest_database_error"
            )
            raise HarvestError(code) from None
        finally:
            if connection is not None:
                connection.close()

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        attempt: HarvestAttempt,
        parser_version: str,
    ) -> HarvestProcessingSnapshot:
        key = HarvestPageRules.key(parser_version)
        row = connection.execute(
            "SELECT * FROM harvest_attempts WHERE id=?", (attempt.attempt_id,)
        ).fetchone()
        if row is None:
            raise HarvestError("attempt_missing")
        envelope = HarvestPageRules.decode(row["response_metadata_json"])
        if (
            set(envelope)
            not in (
                {"format_version", "request", "capture"},
                {"format_version", "request", "capture", "processing"},
            )
            or type(envelope["format_version"]) is not int
            or envelope["format_version"] != 1
            or envelope["request"] != asdict(attempt.request)
            or (None if envelope["capture"] is None else HarvestPageRules.encode(envelope["capture"]))
            != attempt.capture_json
            or (row["unit_id"], row["attempt_no"], row["state"], row["started_at"], row["finished_at"])
            != (attempt.unit_id, attempt.attempt_no, attempt.state, attempt.started_at, attempt.finished_at)
        ):
            raise HarvestError("attempt_snapshot_conflict")
        processing = envelope.get("processing", {})
        if not isinstance(processing, dict) or len(processing) > 32:
            raise HarvestError("invalid_processing_receipt")
        previous = None
        for stored_key, value in processing.items():
            if not isinstance(stored_key, str) or not stored_key.startswith(HarvestPageRules.VERSION + "/"):
                raise HarvestError("invalid_processing_receipt")
            stored_version = stored_key.split("/", 1)[1]
            HarvestPageRules.key(stored_version)
            receipt = HarvestPageRules.receipt(value, attempt.attempt_id, attempt.unit_id, stored_version)
            if stored_key == key:
                previous = receipt
        unit = connection.execute("SELECT * FROM harvest_units WHERE id=?", (attempt.unit_id,)).fetchone()
        if unit is None:
            raise HarvestError("unit_missing")
        binding = connection.execute(
            "SELECT * FROM source_bindings WHERE id=?", (unit["binding_id"],)
        ).fetchone()
        if binding is None or (binding["source"], binding["query_fingerprint"], binding["enabled"]) != (
            attempt.request.source_id,
            attempt.request.query_fingerprint,
            1,
        ):
            raise HarvestError("binding_conflict")
        version, state = unit["checkpoint_version"], unit["state"]
        HarvestPageRules.checkpoint(version)
        if state not in {
            "pending",
            "running",
            "partial",
            "failed",
            "succeeded",
            "verified_empty",
            "unavailable",
        }:
            raise HarvestError("invalid_checkpoint_state")
        coverage = HarvestPageRules.decode(unit["coverage_json"])
        next_start, total = 0, None
        if version == 0:
            if (
                unit["cursor_json"] is not None
                or coverage != {}
                or state in {"succeeded", "verified_empty", "partial"}
            ):
                raise HarvestError("invalid_checkpoint_state")
        else:
            cursor = HarvestPageRules.decode(unit["cursor_json"])
            if (
                set(cursor) != {"format_version", "next_start", "total_results"}
                or type(cursor["format_version"]) is not int
                or cursor["format_version"] != 1
            ):
                raise HarvestError("invalid_checkpoint_state")
            next_start, total = cursor["next_start"], cursor["total_results"]
            if (
                any(
                    type(value) is not int or not 0 <= value <= attempt.request.maximum_window_results
                    for value in (next_start, total)
                )
                or next_start > total
            ):
                raise HarvestError("invalid_checkpoint_state")
            if coverage != {
                "format_version": 1,
                "record_count": next_start,
                "complete": state in {"succeeded", "verified_empty"},
            }:
                raise HarvestError("invalid_checkpoint_state")
            if (
                (state == "verified_empty" and (next_start != 0 or total != 0))
                or (state == "succeeded" and (not total or next_start != total))
                or (state == "partial" and next_start >= total)
            ):
                raise HarvestError("invalid_checkpoint_state")
        records = tuple(
            row[0]
            for row in connection.execute(
                "SELECT native_id FROM source_observations WHERE unit_id=? ORDER BY native_id LIMIT 30001",
                (attempt.unit_id,),
            )
        )
        if len(records) != next_start or len(set(records)) != len(records):
            raise HarvestError("invalid_checkpoint_observations")
        return HarvestProcessingSnapshot(
            attempt,
            version,
            next_start,
            total,
            state,
            parser_version,
            records,
            row["response_metadata_json"],
            unit["cursor_json"],
            unit["coverage_json"],
            previous,
        )

    def snapshot(self, attempt: HarvestAttempt, parser_version: str) -> HarvestProcessingSnapshot:
        with self._transaction(write=False) as connection:
            return self._snapshot(connection, attempt, parser_version)

    def commit(
        self,
        snapshot: HarvestProcessingSnapshot,
        page: ParsedArxivPage | None,
        error_code: str | None,
        processed_at: datetime,
    ) -> HarvestPageResult:
        result = HarvestPageRules.result(snapshot, page, error_code, processed_at)
        with self._transaction(write=True) as connection:
            current = self._snapshot(connection, snapshot.attempt, snapshot.parser_version)
            if current.previous_result is not None:
                previous = current.previous_result
                if previous.expected_checkpoint_version != snapshot.checkpoint_version:
                    raise HarvestError("checkpoint_conflict")
                # Independent concurrent calls must agree on the actual parsed outcome.
                if asdict(previous) | {"processed_at": result.processed_at} != asdict(result):
                    raise HarvestError("processing_result_conflict")
                return previous
            if current != snapshot:
                raise HarvestError("checkpoint_conflict")
            envelope = HarvestPageRules.decode(snapshot.envelope_json)
            processing = envelope.setdefault("processing", {})
            if len(processing) >= 32:
                raise HarvestError("processing_revision_limit")
            processing[HarvestPageRules.key(snapshot.parser_version)] = asdict(result)
            encoded = HarvestPageRules.encode(envelope)
            HarvestPageRules.decode(encoded)
            cursor, coverage = snapshot.cursor_json, snapshot.coverage_json
            if page is not None:
                capture = HarvestPageRules.decode(snapshot.attempt.capture_json or "{}")
                for identity, record in zip(result.observation_ids, page.records, strict=True):
                    connection.execute(
                        "INSERT INTO source_observations(id,unit_id,source,native_id,payload_object_id,"
                        "parser_version,observed_at,native_updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            identity,
                            result.unit_id,
                            snapshot.attempt.request.source_id,
                            record.source_record_id,
                            capture["raw_object_id"],
                            page.parser_version,
                            capture["received_at"],
                            PrepareHarvestCapture.time(record.updated_at),
                        ),
                    )
                cursor = HarvestPageRules.encode(
                    {
                        "format_version": 1,
                        "next_start": result.next_start,
                        "total_results": result.total_results,
                    }
                )
                coverage = HarvestPageRules.encode(
                    {
                        "format_version": 1,
                        "record_count": result.next_start,
                        "complete": result.state in {"succeeded", "verified_empty"},
                    }
                )
            changed = connection.execute(
                "UPDATE harvest_units SET state=?,cursor_json=?,coverage_json=?,checkpoint_version=? "
                "WHERE id=? AND checkpoint_version=?",
                (
                    result.state,
                    cursor,
                    coverage,
                    result.checkpoint_version,
                    result.unit_id,
                    snapshot.checkpoint_version,
                ),
            )
            if changed.rowcount != 1:
                raise HarvestError("checkpoint_conflict")
            changed = connection.execute(
                (
                    "UPDATE harvest_attempts SET response_metadata_json=? WHERE id=? AND re"
                    "sponse_metadata_json=?"
                ),
                (encoded, result.attempt_id, snapshot.envelope_json),
            )
            if changed.rowcount != 1:
                raise HarvestError("attempt_snapshot_conflict")
            return result
