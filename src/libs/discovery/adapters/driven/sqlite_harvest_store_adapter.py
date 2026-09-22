import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime

from libs.discovery.domain.services.prepare_harvest_capture import PrepareHarvestCapture
from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_query_error import SourceQueryError
from libs.discovery.ports.source_query_compiler_port import SourceQueryCompilerPort


class SqliteHarvestStoreAdapter:
    """An attempt journal, not a parser, scheduler, or checkpoint committer."""

    def __init__(self, connect: Callable[[], sqlite3.Connection], compiler: SourceQueryCompilerPort) -> None:
        self._connect = connect
        self._compiler = compiler

    @staticmethod
    def _id(value: str) -> None:
        if not isinstance(value, str) or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9:_-]{0,127}", value) is None:
            raise HarvestError("invalid_attempt_identity")

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

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

    def _attempt(self, connection: sqlite3.Connection, attempt_id: str) -> HarvestAttempt:
        row = connection.execute("SELECT * FROM harvest_attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise HarvestError("attempt_missing")
        try:
            data = json.loads(row["response_metadata_json"])
            if (
                set(data) != {"format_version", "request", "capture"}
                or type(data["format_version"]) is not int
                or data["format_version"] != 1
            ):
                raise HarvestError("invalid_attempt_envelope")
            if row["state"] not in {"running", "captured", "failed"}:
                raise HarvestError("invalid_attempt_state")
            capture = data["capture"]
            if (capture is None) != (row["state"] == "running"):
                raise HarvestError("invalid_attempt_state")
            return HarvestAttempt(
                row["id"],
                row["unit_id"],
                row["attempt_no"],
                SourcePageRequest(**data["request"]),
                row["started_at"],
                row["state"],
                None if capture is None else self._json(capture),
                row["finished_at"],
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise HarvestError("invalid_attempt_envelope") from exc

    def start(
        self, plan: CompiledSourceQuery, request: SourcePageRequest, attempt_id: str, started_at: datetime
    ) -> HarvestAttempt:
        self._id(attempt_id)
        now = PrepareHarvestCapture.time(started_at)
        try:
            if not isinstance(plan, CompiledSourceQuery) or not isinstance(request, SourcePageRequest):
                raise HarvestError("invalid_harvest_request")
            if self._compiler.page(plan, request.start) != request:
                raise HarvestError("invalid_harvest_request")
            data = json.loads(plan.provenance_json)["input"]
            profile, revision = data["profile_id"], data["profile_revision"]
            config_revision = data["domain"]["revision"]
            start = PrepareHarvestCapture.time(datetime.fromisoformat(data["window_start"]))
            end = PrepareHarvestCapture.time(datetime.fromisoformat(data["window_end"]))
            if (
                start >= end
                or not isinstance(profile, str)
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", profile) is None
            ):
                raise HarvestError("invalid_harvest_definition")
            if any(type(value) is not int or not 1 <= value < 2**63 for value in (revision, config_revision)):
                raise HarvestError("invalid_harvest_definition")
        except (KeyError, TypeError, ValueError, SourceQueryError) as exc:
            raise HarvestError("invalid_harvest_definition") from exc
        binding_id = "binding:" + plan.query_fingerprint
        unit_id = "unit:" + plan.query_fingerprint
        with self._transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT 1 FROM harvest_attempts WHERE id=?", (attempt_id,)
            ).fetchone()
            if existing is not None:
                attempt = self._attempt(connection, attempt_id)
                if attempt.unit_id != unit_id or attempt.request != request:
                    raise HarvestError("attempt_conflict")
                return attempt
            binding = connection.execute("SELECT * FROM source_bindings WHERE id=?", (binding_id,)).fetchone()
            expected = (
                profile,
                revision,
                plan.source_id,
                config_revision,
                plan.provenance_json,
                plan.query_fingerprint,
            )
            if binding is None:
                connection.execute(
                    "INSERT INTO source_bindings VALUES(?,?,?,?,?,?,?,1)", (binding_id, *expected)
                )
            elif tuple(binding)[1:7] != expected or binding["enabled"] != 1:
                raise HarvestError("binding_conflict")
            unit = connection.execute("SELECT * FROM harvest_units WHERE id=?", (unit_id,)).fetchone()
            if unit is None:
                connection.execute(
                    (
                        "INSERT INTO harvest_units(id,binding_id,window_start,window_end,state,"
                        "created_at) VALUES(?,?,?,?,'pending',?)"
                    ),
                    (unit_id, binding_id, start, end, now),
                )
            elif (unit["binding_id"], unit["window_start"], unit["window_end"]) != (binding_id, start, end):
                raise HarvestError("unit_conflict")
            elif unit["state"] in {"succeeded", "verified_empty", "unavailable"}:
                raise HarvestError("unit_not_open")
            number = connection.execute(
                "SELECT COALESCE(MAX(attempt_no),0)+1 FROM harvest_attempts WHERE unit_id=?", (unit_id,)
            ).fetchone()[0]
            if number >= 2**63:
                raise HarvestError("attempt_limit")
            envelope = self._json({"format_version": 1, "request": asdict(request), "capture": None})
            connection.execute(
                (
                    "INSERT INTO harvest_attempts(id,unit_id,attempt_no,state,response_meta"
                    "data_json,started_at) VALUES(?,?,?,'running',?,?)"
                ),
                (attempt_id, unit_id, number, envelope, now),
            )
            return self._attempt(connection, attempt_id)

    def read(self, attempt_id: str) -> HarvestAttempt:
        self._id(attempt_id)
        with self._transaction(write=False) as connection:
            return self._attempt(connection, attempt_id)

    def record(self, attempt_id: str, capture_json: str, recorded_at: datetime) -> HarvestAttempt:
        self._id(attempt_id)
        now = PrepareHarvestCapture.time(recorded_at)
        try:
            capture = json.loads(capture_json)
            if (
                not isinstance(capture, dict)
                or capture.get("format_version") != 1
                or self._json(capture) != capture_json
            ):
                raise HarvestError("invalid_capture_metadata")
        except (ValueError, TypeError) as exc:
            raise HarvestError("invalid_capture_metadata") from exc
        with self._transaction(write=True) as connection:
            attempt = self._attempt(connection, attempt_id)
            if (
                capture.get("request_fingerprint") != attempt.request.request_fingerprint
                or now < attempt.started_at
            ):
                raise HarvestError("capture_request_mismatch")
            if attempt.capture_json is not None:
                if attempt.capture_json != capture_json:
                    raise HarvestError("capture_conflict")
                return attempt
            failure = capture.get("failure_code")
            state = "captured" if failure is None else "failed"
            envelope = self._json(
                {"format_version": 1, "request": asdict(attempt.request), "capture": capture}
            )
            connection.execute(
                (
                    "UPDATE harvest_attempts SET state=?,error_code=?,response_metadata_jso"
                    "n=?,finished_at=? WHERE id=?"
                ),
                (state, failure, envelope, now, attempt_id),
            )
            return self._attempt(connection, attempt_id)
