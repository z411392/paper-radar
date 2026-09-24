import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from libs.discovery.domain.services.prepare_harvest_capture import PrepareHarvestCapture
from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.pubmed_bibliography_batch import PubmedBibliographyBatch
from libs.discovery.dtos.pubmed_harvest_state import PendingPubmedBatch, PubmedHarvestState
from libs.discovery.dtos.pubmed_search_page import PubmedSearchPage
from libs.discovery.exceptions.harvest_error import HarvestError


class SqlitePubmedHarvestStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _json(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise HarvestError("invalid_pubmed_harvest_state") from exc

    @classmethod
    def _decode(cls, value: object) -> object:
        if not isinstance(value, str):
            raise HarvestError("invalid_pubmed_harvest_state")
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            raise HarvestError("invalid_pubmed_harvest_state") from None
        if cls._json(decoded) != value:
            raise HarvestError("invalid_pubmed_harvest_state")
        return decoded

    @staticmethod
    def _fingerprint(value: object, code: str = "invalid_pubmed_harvest_state") -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise HarvestError(code)
        return value

    @staticmethod
    def _time(value: datetime) -> str:
        return PrepareHarvestCapture.time(value)

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise HarvestError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise HarvestError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except HarvestError:
            raise
        except sqlite3.IntegrityError as exc:
            raise HarvestError("pubmed_harvest_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "pubmed_harvest_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "pubmed_harvest_database_error"
            )
            raise HarvestError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _definition(
        cls,
        plan: CompiledSourceQuery,
    ) -> tuple[dict[str, object], str, str]:
        if (
            not isinstance(plan, CompiledSourceQuery)
            or plan.source_id != "pubmed"
            or not isinstance(plan.provenance_json, str)
        ):
            raise HarvestError("invalid_harvest_definition")
        cls._fingerprint(plan.query_fingerprint, "invalid_harvest_definition")
        try:
            envelope = json.loads(plan.provenance_json)
            data = envelope["input"]
            domain = data["domain"]
            profile_id = data["profile_id"]
            profile_revision = data["profile_revision"]
            domain_revision = domain["revision"]
            window_start = cls._time(datetime.fromisoformat(data["window_start"]))
            window_end = cls._time(datetime.fromisoformat(data["window_end"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise HarvestError("invalid_harvest_definition") from None
        if (
            not isinstance(data, dict)
            or not isinstance(domain, dict)
            or not isinstance(profile_id, str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", profile_id) is None
            or type(profile_revision) is not int
            or type(domain_revision) is not int
            or not 1 <= profile_revision < 2**63
            or not 1 <= domain_revision < 2**63
            or window_start >= window_end
        ):
            raise HarvestError("invalid_harvest_definition")
        return data, window_start, window_end

    @staticmethod
    def _ids(plan: CompiledSourceQuery) -> tuple[str, str]:
        return "binding:" + plan.query_fingerprint, "unit:" + plan.query_fingerprint

    @classmethod
    def _require_object(
        cls,
        connection: sqlite3.Connection,
        object_id: str,
        expected_sha256: str,
    ) -> None:
        cls._fingerprint(expected_sha256)
        row = connection.execute(
            "SELECT content_sha256,kind,state FROM object_registry WHERE object_id=?",
            (object_id,),
        ).fetchone()
        if (
            row is None
            or row["content_sha256"] != expected_sha256
            or row["kind"] != "raw"
            or row["state"] != "available"
            or object_id != "raw:" + expected_sha256
        ):
            raise HarvestError("pubmed_raw_object_mismatch")

    @classmethod
    def _pmids(cls, text: object) -> tuple[str, ...]:
        decoded = cls._decode(text)
        if (
            not isinstance(decoded, list)
            or len(decoded) > 10000
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9]{1,10}", value) is None
                or int(value) <= 0
                for value in decoded
            )
            or len(decoded) != len(set(decoded))
        ):
            raise HarvestError("invalid_pubmed_harvest_state")
        return tuple(str(int(value)) for value in decoded)

    @classmethod
    def _unit_state(
        cls,
        connection: sqlite3.Connection,
        plan: CompiledSourceQuery,
    ) -> PubmedHarvestState:
        binding_id, unit_id = cls._ids(plan)
        data, window_start, window_end = cls._definition(plan)
        binding = connection.execute(
            "SELECT * FROM source_bindings WHERE id=?",
            (binding_id,),
        ).fetchone()
        unit = connection.execute(
            "SELECT * FROM harvest_units WHERE id=?",
            (unit_id,),
        ).fetchone()
        if binding is None or unit is None:
            raise HarvestError("pubmed_harvest_missing")
        expected_binding = (
            data["profile_id"],
            data["profile_revision"],
            "pubmed",
            data["domain"]["revision"],
            plan.provenance_json,
            plan.query_fingerprint,
            1,
        )
        if tuple(binding)[1:8] != expected_binding:
            raise HarvestError("binding_conflict")
        if (
            unit["binding_id"] != binding_id
            or unit["window_start"] != window_start
            or unit["window_end"] != window_end
        ):
            raise HarvestError("unit_conflict")
        checkpoint = unit["checkpoint_version"]
        if type(checkpoint) is not int or checkpoint < 0:
            raise HarvestError("invalid_pubmed_harvest_state")

        if checkpoint == 0:
            if unit["cursor_json"] is not None or unit["coverage_json"] != "{}":
                raise HarvestError("invalid_pubmed_harvest_state")
            next_start, total = 0, None
        else:
            cursor = cls._decode(unit["cursor_json"])
            coverage = cls._decode(unit["coverage_json"])
            if (
                not isinstance(cursor, dict)
                or set(cursor) != {"format_version", "next_start", "total_results"}
                or cursor["format_version"] != 1
                or type(cursor["next_start"]) is not int
                or type(cursor["total_results"]) is not int
                or not 0 <= cursor["next_start"] <= cursor["total_results"]
                or not isinstance(coverage, dict)
                or set(coverage) != {"format_version", "record_count", "complete"}
                or coverage["format_version"] != 1
                or coverage["record_count"] != cursor["next_start"]
                or type(coverage["complete"]) is not bool
            ):
                raise HarvestError("invalid_pubmed_harvest_state")
            next_start, total = cursor["next_start"], cursor["total_results"]

        page = connection.execute(
            "SELECT * FROM pubmed_harvest_pages WHERE unit_id=? AND start_index=?",
            (unit_id, next_start),
        ).fetchone()
        if page is None:
            return PubmedHarvestState(
                unit_id,
                checkpoint,
                next_start,
                total,
                unit["state"],
                None,
                (),
                0,
            )
        pmids = cls._pmids(page["pmids_json"])
        if (
            page["total_results"] < len(pmids)
            or page["next_batch_offset"] > len(pmids)
            or page["start_index"] != next_start
        ):
            raise HarvestError("invalid_pubmed_harvest_state")
        return PubmedHarvestState(
            unit_id,
            checkpoint,
            next_start,
            total,
            unit["state"],
            page["start_index"],
            pmids,
            page["next_batch_offset"],
        )

    def ensure(
        self,
        plan: CompiledSourceQuery,
        created_at: datetime,
    ) -> PubmedHarvestState:
        data, window_start, window_end = self._definition(plan)
        created = self._time(created_at)
        binding_id, unit_id = self._ids(plan)
        domain = data["domain"]
        with self._transaction(write=True) as connection:
            binding = connection.execute(
                "SELECT * FROM source_bindings WHERE id=?",
                (binding_id,),
            ).fetchone()
            expected = (
                data["profile_id"],
                data["profile_revision"],
                "pubmed",
                domain["revision"],
                plan.provenance_json,
                plan.query_fingerprint,
                1,
            )
            if binding is None:
                connection.execute(
                    "INSERT INTO source_bindings VALUES(?,?,?,?,?,?,?,?)",
                    (binding_id, *expected),
                )
            elif tuple(binding)[1:8] != expected:
                raise HarvestError("binding_conflict")
            unit = connection.execute(
                "SELECT * FROM harvest_units WHERE id=?",
                (unit_id,),
            ).fetchone()
            if unit is None:
                connection.execute(
                    "INSERT INTO harvest_units("
                    "id,binding_id,window_start,window_end,state,cursor_json,"
                    "checkpoint_version,coverage_json,created_at"
                    ") VALUES(?,?,?,?,'pending',NULL,0,'{}',?)",
                    (unit_id, binding_id, window_start, window_end, created),
                )
            elif (
                unit["binding_id"],
                unit["window_start"],
                unit["window_end"],
            ) != (binding_id, window_start, window_end):
                raise HarvestError("unit_conflict")
            return self._unit_state(connection, plan)

    def read(self, plan: CompiledSourceQuery) -> PubmedHarvestState:
        with self._transaction(write=False) as connection:
            return self._unit_state(connection, plan)

    @classmethod
    def _page_fingerprint(
        cls,
        plan: CompiledSourceQuery,
        page: PubmedSearchPage,
    ) -> str:
        value = cls._json(
            {
                "query_fingerprint": plan.query_fingerprint,
                "start_index": page.observation.start_index,
                "total_results": page.observation.total_results,
                "pmids": list(page.pmids),
            }
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _advance_page(
        cls,
        connection: sqlite3.Connection,
        plan: CompiledSourceQuery,
        page_row: sqlite3.Row,
        *,
        updated_at: str,
    ) -> None:
        unit_id = cls._ids(plan)[1]
        unit = connection.execute(
            "SELECT * FROM harvest_units WHERE id=?",
            (unit_id,),
        ).fetchone()
        if unit is None:
            raise HarvestError("pubmed_harvest_missing")
        pmids = cls._pmids(page_row["pmids_json"])
        if page_row["next_batch_offset"] != len(pmids):
            raise HarvestError("pubmed_page_not_complete")
        current = cls._unit_state(connection, plan)
        if current.next_start != page_row["start_index"]:
            raise HarvestError("checkpoint_conflict")
        total = page_row["total_results"]
        following = page_row["start_index"] + len(pmids)
        if following > total:
            raise HarvestError("invalid_pubmed_harvest_state")
        if total == 0:
            state = "verified_empty"
        elif following == total:
            state = "succeeded"
        else:
            state = "partial"
        cursor = cls._json(
            {
                "format_version": 1,
                "next_start": following,
                "total_results": total,
            }
        )
        coverage = cls._json(
            {
                "format_version": 1,
                "record_count": following,
                "complete": state in {"succeeded", "verified_empty"},
            }
        )
        changed = connection.execute(
            "UPDATE harvest_units SET state=?,cursor_json=?,coverage_json=?,"
            "checkpoint_version=checkpoint_version+1 "
            "WHERE id=? AND checkpoint_version=?",
            (state, cursor, coverage, unit_id, unit["checkpoint_version"]),
        ).rowcount
        if changed != 1:
            raise HarvestError("checkpoint_conflict")
        connection.execute(
            "UPDATE pubmed_harvest_pages SET state='complete',updated_at=? "
            "WHERE unit_id=? AND start_index=?",
            (updated_at, unit_id, page_row["start_index"]),
        )

    def save_search(
        self,
        plan: CompiledSourceQuery,
        page: PubmedSearchPage,
        search_object_id: str,
        observed_at: datetime,
    ) -> PubmedHarvestState:
        observed = self._time(observed_at)
        if (
            not isinstance(page, PubmedSearchPage)
            or page.observation.source_id != "pubmed"
            or page.observation.query_fingerprint != plan.query_fingerprint
            or page.observation.record_ids != page.pmids
            or page.request_fingerprint == ""
            or hashlib.sha256(page.raw_body).hexdigest() != page.response_sha256
            or page.observation.total_results > plan.maximum_window_results
            or len(page.pmids) > plan.page_size
        ):
            raise HarvestError("invalid_pubmed_search_page")
        self._fingerprint(page.response_sha256)
        fingerprint = self._page_fingerprint(plan, page)
        pmids_json = self._json(list(page.pmids))

        with self._transaction(write=True) as connection:
            self._require_object(
                connection,
                search_object_id,
                page.response_sha256,
            )
            current = self._unit_state(connection, plan)
            if current.next_start != page.observation.start_index:
                raise HarvestError("checkpoint_offset_conflict")
            if (
                current.total_results is not None
                and current.total_results != page.observation.total_results
            ):
                raise HarvestError("pubmed_result_set_changed")
            if (
                page.pmids
                and page.observation.start_index + len(page.pmids)
                < page.observation.total_results
                and len(page.pmids) < plan.page_size
            ):
                raise HarvestError("incomplete_page")

            existing = connection.execute(
                "SELECT * FROM pubmed_harvest_pages "
                "WHERE unit_id=? AND start_index=?",
                (current.unit_id, current.next_start),
            ).fetchone()
            if existing is not None:
                if (
                    existing["total_results"] != page.observation.total_results
                    or existing["pmids_json"] != pmids_json
                    or existing["page_fingerprint"] != fingerprint
                ):
                    raise HarvestError("pubmed_result_set_changed")
                return self._unit_state(connection, plan)

            state = "complete" if page.observation.total_results == 0 else "searched"
            connection.execute(
                "INSERT INTO pubmed_harvest_pages("
                "unit_id,start_index,total_results,pmids_json,page_fingerprint,"
                "search_object_id,search_sha256,state,next_batch_offset,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,0,?,?)",
                (
                    current.unit_id,
                    current.next_start,
                    page.observation.total_results,
                    pmids_json,
                    fingerprint,
                    search_object_id,
                    page.response_sha256,
                    state,
                    observed,
                    observed,
                ),
            )
            if state == "complete":
                row = connection.execute(
                    "SELECT * FROM pubmed_harvest_pages "
                    "WHERE unit_id=? AND start_index=?",
                    (current.unit_id, current.next_start),
                ).fetchone()
                assert row is not None
                self._advance_page(
                    connection,
                    plan,
                    row,
                    updated_at=observed,
                )
            return self._unit_state(connection, plan)

    def next_batch(
        self,
        plan: CompiledSourceQuery,
        *,
        maximum_batch_size: int,
    ) -> PendingPubmedBatch | None:
        if (
            type(maximum_batch_size) is not int
            or not 1 <= maximum_batch_size <= 200
        ):
            raise HarvestError("invalid_pubmed_batch_size")
        with self._transaction(write=False) as connection:
            state = self._unit_state(connection, plan)
            if state.state in {"succeeded", "verified_empty", "unavailable"}:
                return None
            if state.page_start is None:
                return None
            row = connection.execute(
                "SELECT state,next_batch_offset,pmids_json "
                "FROM pubmed_harvest_pages WHERE unit_id=? AND start_index=?",
                (state.unit_id, state.page_start),
            ).fetchone()
            if row is None:
                raise HarvestError("invalid_pubmed_harvest_state")
            if row["state"] == "complete":
                return None
            pmids = self._pmids(row["pmids_json"])
            offset = row["next_batch_offset"]
            if not 0 <= offset < len(pmids):
                raise HarvestError("invalid_pubmed_harvest_state")
            values = pmids[offset : offset + maximum_batch_size]
            return PendingPubmedBatch(
                state.unit_id,
                state.page_start,
                offset,
                values,
            )

    def save_bibliography(
        self,
        plan: CompiledSourceQuery,
        pending: PendingPubmedBatch,
        batch: PubmedBibliographyBatch,
        payload_object_id: str,
        observed_at: datetime,
    ) -> PubmedHarvestState:
        observed = self._time(observed_at)
        if (
            not isinstance(pending, PendingPubmedBatch)
            or not isinstance(batch, PubmedBibliographyBatch)
            or not pending.pmids
            or batch.parser_version != "pubmed-eutils-parser-v1"
            or hashlib.sha256(batch.raw_body).hexdigest() != batch.response_sha256
        ):
            raise HarvestError("invalid_pubmed_bibliography")
        actual_pmids = tuple(record.pmid for record in batch.records)
        if (
            len(actual_pmids) != len(set(actual_pmids))
            or set(actual_pmids) != set(pending.pmids)
            or len(actual_pmids) != len(pending.pmids)
        ):
            raise HarvestError("pubmed_batch_identity_mismatch")
        self._fingerprint(batch.response_sha256)
        pmids_json = self._json(list(pending.pmids))

        with self._transaction(write=True) as connection:
            self._require_object(
                connection,
                payload_object_id,
                batch.response_sha256,
            )
            state = self._unit_state(connection, plan)
            if (
                pending.unit_id != state.unit_id
                or pending.page_start != state.next_start
            ):
                raise HarvestError("checkpoint_conflict")
            existing_batch = connection.execute(
                "SELECT * FROM pubmed_bibliography_batches "
                "WHERE unit_id=? AND start_index=? AND batch_offset=?",
                (
                    pending.unit_id,
                    pending.page_start,
                    pending.batch_offset,
                ),
            ).fetchone()
            if existing_batch is not None:
                expected = (
                    pmids_json,
                    payload_object_id,
                    batch.response_sha256,
                    batch.parser_version,
                )
                actual = (
                    existing_batch["pmids_json"],
                    existing_batch["payload_object_id"],
                    existing_batch["response_sha256"],
                    existing_batch["parser_version"],
                )
                if actual != expected:
                    raise HarvestError("pubmed_batch_conflict")
                return self._unit_state(connection, plan)

            page = connection.execute(
                "SELECT * FROM pubmed_harvest_pages "
                "WHERE unit_id=? AND start_index=?",
                (pending.unit_id, pending.page_start),
            ).fetchone()
            if (
                page is None
                or page["state"] == "complete"
                or page["next_batch_offset"] != pending.batch_offset
            ):
                raise HarvestError("checkpoint_conflict")
            page_pmids = self._pmids(page["pmids_json"])
            expected_pmids = page_pmids[
                pending.batch_offset : pending.batch_offset + len(pending.pmids)
            ]
            if expected_pmids != pending.pmids:
                raise HarvestError("pubmed_batch_identity_mismatch")

            placeholders = ",".join("?" for _ in pending.pmids)
            duplicates = connection.execute(
                "SELECT native_id FROM source_observations "
                "WHERE unit_id=? AND source='pubmed' "
                f"AND native_id IN ({placeholders})",
                (pending.unit_id, *pending.pmids),
            ).fetchall()
            if duplicates:
                raise HarvestError("duplicate_traversal_identity")

            connection.execute(
                "INSERT INTO pubmed_bibliography_batches("
                "unit_id,start_index,batch_offset,pmids_json,payload_object_id,"
                "response_sha256,parser_version,observed_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    pending.unit_id,
                    pending.page_start,
                    pending.batch_offset,
                    pmids_json,
                    payload_object_id,
                    batch.response_sha256,
                    batch.parser_version,
                    observed,
                ),
            )
            records = {record.pmid: record for record in batch.records}
            for pmid in pending.pmids:
                record = records[pmid]
                identity = "observation:" + hashlib.sha256(
                    self._json(
                        [
                            pending.unit_id,
                            "pubmed",
                            pmid,
                            payload_object_id,
                            batch.parser_version,
                        ]
                    ).encode("utf-8")
                ).hexdigest()
                connection.execute(
                    "INSERT INTO source_observations("
                    "id,unit_id,source,native_id,payload_object_id,"
                    "parser_version,observed_at,native_updated_at"
                    ") VALUES(?,?,?,?,?,?,?,NULL)",
                    (
                        identity,
                        pending.unit_id,
                        "pubmed",
                        pmid,
                        payload_object_id,
                        batch.parser_version,
                        observed,
                    ),
                )

            next_offset = pending.batch_offset + len(pending.pmids)
            page_state = (
                "complete"
                if next_offset == len(page_pmids)
                else "bibliography_partial"
            )
            connection.execute(
                "UPDATE pubmed_harvest_pages SET state=?,next_batch_offset=?,updated_at=? "
                "WHERE unit_id=? AND start_index=? AND next_batch_offset=?",
                (
                    page_state,
                    next_offset,
                    observed,
                    pending.unit_id,
                    pending.page_start,
                    pending.batch_offset,
                ),
            )
            if page_state == "complete":
                updated = connection.execute(
                    "SELECT * FROM pubmed_harvest_pages "
                    "WHERE unit_id=? AND start_index=?",
                    (pending.unit_id, pending.page_start),
                ).fetchone()
                assert updated is not None
                self._advance_page(
                    connection,
                    plan,
                    updated,
                    updated_at=observed,
                )
            return self._unit_state(connection, plan)
