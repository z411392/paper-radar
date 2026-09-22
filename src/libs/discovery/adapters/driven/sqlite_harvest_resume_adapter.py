import sqlite3
from collections.abc import Callable
from datetime import datetime

from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.domain.services.harvest_page_rules import HarvestPageRules
from libs.discovery.domain.services.prepare_harvest_capture import PrepareHarvestCapture
from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.harvest_resume_state import HarvestResumeState
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.ports.source_query_compiler_port import SourceQueryCompilerPort


class SqliteHarvestResumeAdapter(SqliteHarvestProcessingAdapter):
    """Reuse this owner's snapshot checks inside one read transaction; never claim a job."""

    def __init__(self, connect: Callable[[], sqlite3.Connection], compiler: SourceQueryCompilerPort) -> None:
        super().__init__(connect)
        self._compiler = compiler

    def _restore_attempt(self, row: sqlite3.Row, plan: CompiledSourceQuery) -> HarvestAttempt:
        envelope = HarvestPageRules.decode(row["response_metadata_json"])
        try:
            request = SourcePageRequest(**envelope["request"])
            capture = envelope["capture"]
            if (
                self._compiler.page(plan, request.start) != request
                or row["unit_id"] != "unit:" + plan.query_fingerprint
                or type(row["attempt_no"]) is not int
                or row["attempt_no"] < 1
                or row["state"] not in {"running", "captured", "failed"}
                or (capture is None) != (row["state"] == "running")
                or (row["finished_at"] is None) != (capture is None)
            ):
                raise HarvestError("invalid_attempt_envelope")
            started = PrepareHarvestCapture.time(datetime.fromisoformat(row["started_at"]))
            if started != row["started_at"]:
                raise HarvestError("invalid_harvest_time")
            if row["finished_at"] is not None:
                finished = PrepareHarvestCapture.time(datetime.fromisoformat(row["finished_at"]))
                if finished != row["finished_at"] or finished < started:
                    raise HarvestError("invalid_harvest_time")
            return HarvestAttempt(
                row["id"],
                row["unit_id"],
                row["attempt_no"],
                request,
                started,
                row["state"],
                None if capture is None else HarvestPageRules.encode(capture),
                row["finished_at"],
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise HarvestError("invalid_attempt_envelope") from None

    def resume(self, plan: CompiledSourceQuery, parser_version: str) -> HarvestResumeState:
        self._compiler.page(plan)
        HarvestPageRules.key(parser_version)
        definition = HarvestPageRules.decode(plan.provenance_json)["input"]
        binding_id, unit_id = "binding:" + plan.query_fingerprint, "unit:" + plan.query_fingerprint
        with self._transaction(write=False) as connection:
            binding = connection.execute("SELECT * FROM source_bindings WHERE id=?", (binding_id,)).fetchone()
            unit = connection.execute("SELECT * FROM harvest_units WHERE id=?", (unit_id,)).fetchone()
            if binding is None and unit is None:
                return HarvestResumeState(unit_id, plan.query_fingerprint, 0, 0, None, "pending")
            if binding is None:
                raise HarvestError("binding_conflict")
            if (
                binding["compiled_query_json"] != plan.provenance_json
                or binding["query_fingerprint"] != plan.query_fingerprint
                or binding["source"] != plan.source_id
                or binding["enabled"] != 1
                or (binding["profile_id"], binding["profile_revision"], binding["config_revision"])
                != (
                    definition["profile_id"],
                    definition["profile_revision"],
                    definition["domain"]["revision"],
                )
            ):
                raise HarvestError("binding_conflict")
            if unit is None:
                raise HarvestError("resume_unit_missing")
            expected_window = tuple(
                PrepareHarvestCapture.time(datetime.fromisoformat(definition[key]))
                for key in ("window_start", "window_end")
            )
            if (
                unit["binding_id"] != binding_id
                or (unit["window_start"], unit["window_end"]) != expected_window
            ):
                raise HarvestError("unit_conflict")
            latest = connection.execute(
                "SELECT * FROM harvest_attempts WHERE unit_id=? ORDER BY attempt_no DESC LIMIT 1", (unit_id,)
            ).fetchone()
            if latest is None:
                raise HarvestError("resume_attempt_missing")
            anchor = self._restore_attempt(latest, plan)
            snapshot = self._snapshot(connection, anchor, parser_version)
            attempt, previous = None, None
            if snapshot.unit_state not in {"succeeded", "verified_empty", "unavailable"}:
                chosen = connection.execute(
                    "SELECT * FROM harvest_attempts WHERE unit_id=? "
                    "AND json_extract(response_metadata_json,'$.request.start')=? "
                    "ORDER BY CASE state WHEN 'captured' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END, "
                    "attempt_no DESC LIMIT 1",
                    (unit_id, snapshot.next_start),
                ).fetchone()
                if chosen is not None:
                    attempt = self._restore_attempt(chosen, plan)
                    snapshot = self._snapshot(connection, attempt, parser_version)
                    previous = snapshot.previous_result
            return HarvestResumeState(
                unit_id,
                plan.query_fingerprint,
                snapshot.checkpoint_version,
                snapshot.next_start,
                snapshot.total_results,
                snapshot.unit_state,
                attempt,
                previous,
            )
