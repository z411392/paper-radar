import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone

from libs.discovery.dtos.harvest_coverage import (
    CrossrefCoverageGeneration,
    CrossrefCoveragePassEvidence,
    HarvestCoverageWindow,
)
from libs.discovery.exceptions.harvest_coverage_error import HarvestCoverageError


class SqliteHarvestCoverageAdapter:
    _PAYLOAD_KEYS = {
        "binding_key",
        "profile_id",
        "profile_revision",
        "domain_id",
        "domain_revision",
        "source_id",
        "window_start",
        "window_end",
    }

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    @staticmethod
    def _text(value: object) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 512
            or "\0" in value
        ):
            raise HarvestCoverageError("harvest_coverage_payload_corrupt")
        return value

    @staticmethod
    def _instant(value: object) -> datetime:
        if not isinstance(value, str):
            raise HarvestCoverageError("harvest_coverage_payload_corrupt")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise HarvestCoverageError("harvest_coverage_payload_corrupt") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise HarvestCoverageError("harvest_coverage_payload_corrupt")
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _payload(cls, encoded: object) -> dict[str, object]:
        if not isinstance(encoded, str):
            raise HarvestCoverageError("harvest_coverage_payload_corrupt")
        try:
            data = json.loads(encoded, object_pairs_hook=cls._object)
        except (json.JSONDecodeError, ValueError, RecursionError):
            raise HarvestCoverageError("harvest_coverage_payload_corrupt") from None
        if not isinstance(data, dict) or set(data) != cls._PAYLOAD_KEYS:
            raise HarvestCoverageError("harvest_coverage_payload_corrupt")
        for key in ("binding_key", "profile_id", "domain_id", "source_id"):
            cls._text(data[key])
        for key in ("profile_revision", "domain_revision"):
            if type(data[key]) is not int or data[key] < 1:
                raise HarvestCoverageError("harvest_coverage_payload_corrupt")
        if data["source_id"] not in {"arxiv", "pubmed", "crossref"}:
            raise HarvestCoverageError("harvest_coverage_payload_corrupt")
        start = cls._instant(data["window_start"])
        end = cls._instant(data["window_end"])
        if start >= end:
            raise HarvestCoverageError("harvest_coverage_payload_corrupt")
        return data

    @staticmethod
    def _latest_error(
        connection: sqlite3.Connection,
        job_id: str,
    ) -> str | None:
        row = connection.execute(
            "SELECT error_code FROM job_attempts "
            "WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None or row["error_code"] is None:
            return None
        if not isinstance(row["error_code"], str) or not row["error_code"].strip():
            raise HarvestCoverageError("harvest_coverage_database_corrupt")
        return row["error_code"]

    @classmethod
    def _matching_crossref_windows(
        cls,
        connection: sqlite3.Connection,
        *,
        binding_key: str,
        start: datetime,
        end: datetime,
    ) -> tuple[sqlite3.Row, ...]:
        rows = connection.execute(
            "SELECT id,query_fingerprint,config_version,rows,state,from_index,until_index "
            "FROM crossref_harvest_windows WHERE binding_key=? ORDER BY created_at,id",
            (binding_key,),
        ).fetchall()
        matched = []
        for row in rows:
            try:
                from_index = cls._instant(row["from_index"])
                until_index = cls._instant(row["until_index"])
            except HarvestCoverageError as exc:
                raise HarvestCoverageError("harvest_coverage_database_corrupt") from exc
            if from_index == start and until_index == end:
                matched.append(row)
        return tuple(matched)

    @staticmethod
    def _crossref_passes(
        connection: sqlite3.Connection,
        window_id: str,
    ) -> tuple[CrossrefCoveragePassEvidence, ...]:
        rows = connection.execute(
            "SELECT pass_no,state,traversal_complete,accounting_complete,"
            "first_reported_total,raw_item_count,processed_item_count,"
            "quarantine_count,unique_doi_count,duplicate_doi_count,parse_gap_count,"
            "drift_suspected,repair_pending,source_completeness,error_code "
            "FROM crossref_harvest_passes WHERE window_id=? ORDER BY pass_no",
            (window_id,),
        ).fetchall()
        result = []
        for row in rows:
            flags = (
                row["traversal_complete"],
                row["accounting_complete"],
                row["drift_suspected"],
                row["repair_pending"],
            )
            counts = (
                row["pass_no"],
                row["raw_item_count"],
                row["processed_item_count"],
                row["quarantine_count"],
                row["unique_doi_count"],
                row["duplicate_doi_count"],
                row["parse_gap_count"],
            )
            if (
                any(value not in {0, 1} for value in flags)
                or any(type(value) is not int or value < 0 for value in counts)
                or row["pass_no"] < 1
                or (
                    row["first_reported_total"] is not None
                    and (
                        type(row["first_reported_total"]) is not int
                        or row["first_reported_total"] < 0
                    )
                )
            ):
                raise HarvestCoverageError("harvest_coverage_database_corrupt")
            result.append(
                CrossrefCoveragePassEvidence(
                    pass_no=row["pass_no"],
                    state=row["state"],
                    traversal_complete=bool(row["traversal_complete"]),
                    accounting_complete=bool(row["accounting_complete"]),
                    first_reported_total=row["first_reported_total"],
                    raw_item_count=row["raw_item_count"],
                    processed_item_count=row["processed_item_count"],
                    quarantine_count=row["quarantine_count"],
                    unique_doi_count=row["unique_doi_count"],
                    duplicate_doi_count=row["duplicate_doi_count"],
                    parse_gap_count=row["parse_gap_count"],
                    drift_suspected=bool(row["drift_suspected"]),
                    repair_pending=bool(row["repair_pending"]),
                    source_completeness=row["source_completeness"],
                    error_code=row["error_code"],
                )
            )
        return tuple(result)

    @staticmethod
    def _provider_revision_changes(
        connection: sqlite3.Connection,
        window_id: str,
    ) -> int:
        value = connection.execute(
            "SELECT count(DISTINCT o.provider_revision_id) "
            "FROM crossref_provider_observations o "
            "JOIN crossref_provider_revisions r ON r.id=o.provider_revision_id "
            "JOIN crossref_harvest_pages p ON p.id=o.page_id "
            "JOIN crossref_harvest_passes hp ON hp.id=p.pass_id "
            "WHERE hp.window_id=? AND r.revision_no>1",
            (window_id,),
        ).fetchone()[0]
        if type(value) is not int or value < 0:
            raise HarvestCoverageError("harvest_coverage_database_corrupt")
        return value

    @staticmethod
    def _stream_watermark(
        connection: sqlite3.Connection,
        *,
        binding_key: str,
        config_version: str,
        rows: int,
    ) -> str | None:
        values = connection.execute(
            "SELECT w.finalized_until FROM crossref_streams s "
            "JOIN crossref_binding_watermarks w ON w.stream_id=s.id "
            "WHERE s.binding_key=? AND s.config_version=? AND s.rows=?",
            (binding_key, config_version, rows),
        ).fetchall()
        if len(values) > 1:
            raise HarvestCoverageError("harvest_coverage_database_corrupt")
        return None if not values else values[0]["finalized_until"]

    @classmethod
    def _crossref_generations(
        cls,
        connection: sqlite3.Connection,
        *,
        binding_key: str,
        start: datetime,
        end: datetime,
    ) -> tuple[CrossrefCoverageGeneration, ...]:
        result = []
        for row in cls._matching_crossref_windows(
            connection,
            binding_key=binding_key,
            start=start,
            end=end,
        ):
            if (
                type(row["rows"]) is not int
                or not 1 <= row["rows"] <= 1000
                or not isinstance(row["query_fingerprint"], str)
                or not isinstance(row["config_version"], str)
            ):
                raise HarvestCoverageError("harvest_coverage_database_corrupt")
            result.append(
                CrossrefCoverageGeneration(
                    window_id=row["id"],
                    query_fingerprint=row["query_fingerprint"],
                    config_version=row["config_version"],
                    requested_rows=row["rows"],
                    window_state=row["state"],
                    changed_provider_revision_count=cls._provider_revision_changes(
                        connection,
                        row["id"],
                    ),
                    stream_finalized_until=cls._stream_watermark(
                        connection,
                        binding_key=binding_key,
                        config_version=row["config_version"],
                        rows=row["rows"],
                    ),
                    safety_lag_seconds=None,
                    passes=cls._crossref_passes(connection, row["id"]),
                )
            )
        return tuple(result)

    def read(self) -> tuple[HarvestCoverageWindow, ...]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise HarvestCoverageError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            jobs = connection.execute(
                "SELECT id,business_key,input_json,state FROM workflow_jobs "
                "WHERE job_kind='harvest_window' ORDER BY business_key"
            ).fetchall()
            result = []
            for job in jobs:
                data = self._payload(job["input_json"])
                start = self._instant(data["window_start"])
                end = self._instant(data["window_end"])
                source_id = str(data["source_id"])
                binding_key = str(data["binding_key"])
                crossref = (
                    self._crossref_generations(
                        connection,
                        binding_key=binding_key,
                        start=start,
                        end=end,
                    )
                    if source_id == "crossref"
                    else ()
                )
                result.append(
                    HarvestCoverageWindow(
                        business_key=job["business_key"],
                        binding_key=binding_key,
                        profile_id=str(data["profile_id"]),
                        profile_revision=int(data["profile_revision"]),
                        domain_id=str(data["domain_id"]),
                        domain_revision=int(data["domain_revision"]),
                        source_id=source_id,
                        window_start=start.isoformat(),
                        window_end=end.isoformat(),
                        workflow_state=job["state"],
                        last_error_code=self._latest_error(connection, job["id"]),
                        population_recall=None,
                        crossref_generations=crossref,
                    )
                )
            connection.commit()
            return tuple(result)
        except HarvestCoverageError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise HarvestCoverageError("harvest_coverage_database_error") from exc
        finally:
            if connection is not None:
                connection.close()
