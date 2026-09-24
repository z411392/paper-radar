import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from libs.discovery.adapters.driven.crossref_window_topology import CrossrefWindowTopology
from libs.discovery.domain.services.crossref_repair_rules import CrossrefRepairRules as Rules
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_repair import (
    CrossrefBindingWatermark,
    CrossrefRepairCandidate,
    CrossrefRepairPolicy,
    CrossrefRepairRun,
    CrossrefWindowFinalization,
)
from libs.discovery.exceptions.crossref_repair_error import CrossrefRepairError


class SqliteCrossrefRepairStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise CrossrefRepairError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise CrossrefRepairError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except CrossrefRepairError:
            raise
        except sqlite3.IntegrityError as exc:
            raise CrossrefRepairError("crossref_repair_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "crossref_repair_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "crossref_repair_database_error"
            )
            raise CrossrefRepairError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _time(value: datetime) -> str:
        return Rules.instant(value).isoformat()

    @staticmethod
    def _hash(*parts: object) -> str:
        payload = json.dumps(
            parts,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def _plan(
        cls,
        plan: CrossrefWindowPlan,
    ) -> dict[str, Any]:
        binding, config, rows, scope_sha, query, parameters, bounds = Rules.plan(plan)
        start, end = bounds.split("\0", 1)
        return {
            "binding_key": binding,
            "config_version": config,
            "rows": rows,
            "scope_sha256": scope_sha,
            "query_fingerprint": query,
            "parameters_fingerprint": parameters,
            "from_index": start,
            "until_index": end,
            "stream_id": Rules.stream_id(plan),
        }

    @staticmethod
    def _repair(row: sqlite3.Row, pass_row: sqlite3.Row) -> CrossrefRepairRun:
        return CrossrefRepairRun(
            row["id"],
            row["window_id"],
            row["repair_no"],
            row["pass_id"],
            pass_row["pass_no"],
            row["state"],
            row["reason"],
            row["error_code"],
        )

    @staticmethod
    def _finalization(row: sqlite3.Row) -> CrossrefWindowFinalization:
        return CrossrefWindowFinalization(
            row["id"],
            row["window_id"],
            row["stream_id"],
            row["generation"],
            row["pass_id"],
            row["reason"],
            Rules.parse_instant(row["finalized_at"]),
        )

    @staticmethod
    def _watermark(row: sqlite3.Row) -> CrossrefBindingWatermark:
        return CrossrefBindingWatermark(
            row["stream_id"],
            row["binding_key"],
            row["config_version"],
            row["rows"],
            Rules.parse_instant(row["finalized_until"]),
            row["latest_window_id"],
            row["latest_finalization_id"],
            Rules.parse_instant(row["updated_at"]),
        )

    def ensure_stream(
        self,
        plan: CrossrefWindowPlan,
        created_at: datetime,
    ) -> str:
        values = self._plan(plan)
        created = self._time(created_at)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM crossref_streams "
                "WHERE binding_key=? AND config_version=? AND rows=?",
                (
                    values["binding_key"],
                    values["config_version"],
                    values["rows"],
                ),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO crossref_streams("
                    "id,binding_key,config_version,rows,scope_sha256,created_at"
                    ") VALUES(?,?,?,?,?,?)",
                    (
                        values["stream_id"],
                        values["binding_key"],
                        values["config_version"],
                        values["rows"],
                        values["scope_sha256"],
                        created,
                    ),
                )
                return values["stream_id"]
            if (
                row["id"] != values["stream_id"]
                or row["scope_sha256"] != values["scope_sha256"]
            ):
                raise CrossrefRepairError(
                    "crossref_stream_definition_conflict"
                )
            return row["id"]

    @classmethod
    def _require_window(
        cls,
        connection: sqlite3.Connection,
        plan: CrossrefWindowPlan,
        window_id: str,
    ) -> sqlite3.Row:
        values = cls._plan(plan)
        row = connection.execute(
            "SELECT * FROM crossref_harvest_windows WHERE id=?",
            (window_id,),
        ).fetchone()
        if row is None:
            raise CrossrefRepairError("crossref_window_missing")
        expected = (
            values["binding_key"],
            values["query_fingerprint"],
            values["config_version"],
            values["from_index"],
            values["until_index"],
            values["rows"],
        )
        actual = (
            row["binding_key"],
            row["query_fingerprint"],
            row["config_version"],
            row["from_index"],
            row["until_index"],
            row["rows"],
        )
        if actual != expected:
            raise CrossrefRepairError("crossref_window_plan_mismatch")
        return row


    def list_finalizable(
        self,
        plan: CrossrefWindowPlan,
        *,
        now: datetime,
        policy: CrossrefRepairPolicy,
    ) -> tuple[CrossrefRepairCandidate, ...]:
        checked = Rules.policy(policy)
        current = Rules.instant(now)
        values = self._plan(plan)
        stream_id = self.ensure_stream(plan, current)
        cutoff = current - timedelta(seconds=checked.safety_lag_seconds)
        with self._transaction(write=False) as connection:
            windows = connection.execute(
                "SELECT * FROM crossref_harvest_windows "
                "WHERE binding_key=? AND config_version=? AND rows=? "
                "AND state='traversed' "
                "AND julianday(until_index)<=julianday(?) "
                "ORDER BY julianday(from_index),julianday(until_index),id",
                (
                    values["binding_key"],
                    values["config_version"],
                    values["rows"],
                    cutoff.isoformat(),
                ),
            ).fetchall()
            windows = CrossrefWindowTopology.leaves(connection, windows)
            result = []
            for window in windows:
                if connection.execute(
                    "SELECT 1 FROM crossref_repair_runs "
                    "WHERE window_id=? AND state='running' LIMIT 1",
                    (window["id"],),
                ).fetchone() is not None:
                    continue
                latest = connection.execute(
                    "SELECT * FROM crossref_harvest_passes "
                    "WHERE window_id=? ORDER BY pass_no DESC LIMIT 1",
                    (window["id"],),
                ).fetchone()
                if (
                    latest is None
                    or latest["state"] != "completed"
                    or not bool(latest["traversal_complete"])
                    or not bool(latest["accounting_complete"])
                    or bool(latest["repair_pending"])
                    or bool(latest["drift_suspected"])
                    or latest["parse_gap_count"] != 0
                    or latest["source_completeness"] != "provisional"
                ):
                    continue
                already = connection.execute(
                    "SELECT 1 FROM crossref_window_finalizations "
                    "WHERE stream_id=? AND pass_id=? LIMIT 1",
                    (stream_id, latest["id"]),
                ).fetchone()
                if already is not None:
                    continue
                result.append(
                    CrossrefRepairCandidate(
                        window["id"],
                        "finalize",
                        Rules.parse_instant(window["from_index"]),
                        Rules.parse_instant(window["until_index"]),
                    )
                )
            return tuple(result[: checked.max_windows])


    @staticmethod
    def _repair_retry_allowed(
        connection: sqlite3.Connection,
        window_id: str,
        current: datetime,
        policy: CrossrefRepairPolicy,
    ) -> bool:
        rows = connection.execute(
            "SELECT state,finished_at FROM crossref_repair_runs "
            "WHERE window_id=? ORDER BY repair_no DESC",
            (window_id,),
        ).fetchall()
        consecutive = 0
        latest_failed_at = None
        for row in rows:
            if row["state"] != "failed":
                break
            if row["finished_at"] is None:
                raise CrossrefRepairError("crossref_repair_state_corrupt")
            if latest_failed_at is None:
                latest_failed_at = Rules.parse_instant(row["finished_at"])
            consecutive += 1
        if consecutive >= policy.max_consecutive_failures:
            raise CrossrefRepairError("crossref_repair_retry_exhausted")
        if latest_failed_at is None:
            return True
        retry_at = latest_failed_at + timedelta(
            seconds=policy.repair_retry_after_seconds
        )
        return current >= retry_at

    def list_candidates(
        self,
        plan: CrossrefWindowPlan,
        *,
        now: datetime,
        policy: CrossrefRepairPolicy,
    ) -> tuple[CrossrefRepairCandidate, ...]:
        checked = Rules.policy(policy)
        current = Rules.instant(now)
        values = self._plan(plan)
        stream_id = self.ensure_stream(plan, current)
        safety_cutoff = current - timedelta(
            seconds=checked.safety_lag_seconds
        )
        periodic_cutoff = current - timedelta(
            seconds=checked.periodic_repair_after_seconds
        )
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                "SELECT w.*,"
                "(SELECT MAX(f.finalized_at) FROM crossref_window_finalizations f "
                "WHERE f.window_id=w.id AND f.stream_id=?) AS last_finalized_at,"
                "EXISTS(SELECT 1 FROM crossref_repair_runs r "
                "WHERE r.window_id=w.id AND r.state='running') AS repair_running "
                "FROM crossref_harvest_windows w "
                "WHERE w.binding_key=? AND w.config_version=? AND w.rows=? "
                "AND julianday(w.until_index)<=julianday(?) "
                "ORDER BY julianday(w.until_index) DESC,w.id",
                (
                    stream_id,
                    values["binding_key"],
                    values["config_version"],
                    values["rows"],
                    safety_cutoff.isoformat(),
                ),
            ).fetchall()
            rows = CrossrefWindowTopology.leaves(connection, rows)
            finalized_recent = [
                row["id"]
                for row in rows
                if row["last_finalized_at"] is not None
            ][: checked.lookback_windows]
            candidates = []
            for row in rows:
                if row["repair_running"]:
                    continue
                if not self._repair_retry_allowed(
                    connection,
                    row["id"],
                    current,
                    checked,
                ):
                    continue
                reason = None
                if row["state"] == "repair_pending":
                    reason = "repair_pending"
                elif (
                    row["id"] in finalized_recent
                    and row["last_finalized_at"] is not None
                    and Rules.parse_instant(row["last_finalized_at"])
                    <= periodic_cutoff
                ):
                    reason = "periodic_recent_window"
                if reason is not None:
                    candidates.append(
                        CrossrefRepairCandidate(
                            row["id"],
                            reason,
                            Rules.parse_instant(row["from_index"]),
                            Rules.parse_instant(row["until_index"]),
                        )
                    )
            candidates.sort(
                key=lambda item: (
                    0 if item.reason == "repair_pending" else 1,
                    -item.until_index.timestamp(),
                    item.window_id,
                )
            )
            return tuple(candidates[: checked.max_windows])

    def start_repair(
        self,
        plan: CrossrefWindowPlan,
        window_id: str,
        *,
        reason: str,
        started_at: datetime,
    ) -> CrossrefRepairRun:
        reason = Rules.reason(reason)
        started = Rules.instant(started_at)
        self.ensure_stream(plan, started)
        values = self._plan(plan)
        with self._transaction(write=True) as connection:
            window = self._require_window(connection, plan, window_id)
            CrossrefWindowTopology.require_leaf(connection, window_id)
            active = connection.execute(
                "SELECT r.*,p.pass_no FROM crossref_repair_runs r "
                "JOIN crossref_harvest_passes p ON p.id=r.pass_id "
                "WHERE r.window_id=? AND r.state='running' "
                "ORDER BY r.repair_no DESC LIMIT 1",
                (window_id,),
            ).fetchone()
            if active is not None:
                pass_row = connection.execute(
                    "SELECT * FROM crossref_harvest_passes WHERE id=?",
                    (active["pass_id"],),
                ).fetchone()
                assert pass_row is not None
                if (
                    active["reason"] == reason
                    and pass_row["parameters_fingerprint"]
                    == values["parameters_fingerprint"]
                ):
                    return self._repair(active, pass_row)
                raise CrossrefRepairError("crossref_repair_already_running")
            latest = connection.execute(
                "SELECT * FROM crossref_harvest_passes "
                "WHERE window_id=? ORDER BY pass_no DESC LIMIT 1",
                (window_id,),
            ).fetchone()
            if latest is None:
                raise CrossrefRepairError("crossref_repair_pass_missing")
            if latest["state"] == "running":
                raise CrossrefRepairError("crossref_repair_pass_running")
            if reason == "repair_pending" and window["state"] != "repair_pending":
                raise CrossrefRepairError("crossref_repair_reason_mismatch")
            if reason == "periodic_recent_window":
                finalization = connection.execute(
                    "SELECT 1 FROM crossref_window_finalizations "
                    "WHERE window_id=? LIMIT 1",
                    (window_id,),
                ).fetchone()
                if finalization is None:
                    raise CrossrefRepairError(
                        "crossref_repair_reason_mismatch"
                    )
            pass_no = latest["pass_no"] + 1
            pass_id = "crossref-pass:" + self._hash(
                window_id,
                pass_no,
                values["parameters_fingerprint"],
            )
            repair_no = connection.execute(
                "SELECT COALESCE(MAX(repair_no),0)+1 "
                "FROM crossref_repair_runs WHERE window_id=?",
                (window_id,),
            ).fetchone()[0]
            repair_id = "crossref-repair:" + self._hash(
                window_id,
                repair_no,
                pass_id,
                reason,
            )
            connection.execute(
                "INSERT INTO crossref_harvest_passes("
                "id,window_id,pass_no,parameters_fingerprint,state,"
                "current_cursor,started_at"
                ") VALUES(?,?,?,?,'running','*',?)",
                (
                    pass_id,
                    window_id,
                    pass_no,
                    values["parameters_fingerprint"],
                    started.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO crossref_repair_runs("
                "id,window_id,repair_no,pass_id,reason,state,created_at"
                ") VALUES(?,?,?,?,?,'running',?)",
                (
                    repair_id,
                    window_id,
                    repair_no,
                    pass_id,
                    reason,
                    started.isoformat(),
                ),
            )
            repair = connection.execute(
                "SELECT * FROM crossref_repair_runs WHERE id=?",
                (repair_id,),
            ).fetchone()
            pass_row = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (pass_id,),
            ).fetchone()
            assert repair is not None and pass_row is not None
            return self._repair(repair, pass_row)

    def reconcile_repair(
        self,
        repair_id: str,
        finished_at: datetime,
    ) -> CrossrefRepairRun:
        Rules.text(repair_id, "invalid_crossref_repair_id", 256)
        finished = Rules.instant(finished_at)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM crossref_repair_runs WHERE id=?",
                (repair_id,),
            ).fetchone()
            if row is None:
                raise CrossrefRepairError("crossref_repair_missing")
            pass_row = connection.execute(
                "SELECT * FROM crossref_harvest_passes WHERE id=?",
                (row["pass_id"],),
            ).fetchone()
            if pass_row is None:
                raise CrossrefRepairError("crossref_repair_state_corrupt")
            if row["state"] in {"completed", "failed"}:
                return self._repair(row, pass_row)
            if pass_row["state"] == "running":
                return self._repair(row, pass_row)
            if pass_row["finished_at"] is None:
                raise CrossrefRepairError("crossref_repair_state_corrupt")
            pass_finished = Rules.parse_instant(pass_row["finished_at"])
            if finished < pass_finished:
                raise CrossrefRepairError("invalid_crossref_repair_time")
            if pass_row["state"] == "completed":
                state, error = "completed", None
            elif pass_row["state"] == "failed":
                state, error = "failed", pass_row["error_code"]
                if not isinstance(error, str) or not error:
                    raise CrossrefRepairError(
                        "crossref_repair_state_corrupt"
                    )
            else:
                raise CrossrefRepairError("crossref_repair_state_corrupt")
            connection.execute(
                "UPDATE crossref_repair_runs SET state=?,finished_at=?,"
                "error_code=? WHERE id=? AND state='running'",
                (state, finished.isoformat(), error, repair_id),
            )
            updated = connection.execute(
                "SELECT * FROM crossref_repair_runs WHERE id=?",
                (repair_id,),
            ).fetchone()
            assert updated is not None
            return self._repair(updated, pass_row)

    @classmethod
    def _advance_watermark(
        cls,
        connection: sqlite3.Connection,
        stream_id: str,
        values: dict[str, Any],
        updated_at: str,
    ) -> None:
        windows = connection.execute(
            "SELECT * FROM crossref_harvest_windows "
            "WHERE binding_key=? AND config_version=? AND rows=? "
            "ORDER BY julianday(from_index),julianday(until_index),id",
            (
                values["binding_key"],
                values["config_version"],
                values["rows"],
            ),
        ).fetchall()
        windows = CrossrefWindowTopology.leaves(connection, windows)
        current_until = None
        latest_window_id = latest_finalization_id = None
        for window in windows:
            finalization = connection.execute(
                "SELECT * FROM crossref_window_finalizations "
                "WHERE window_id=? AND stream_id=? "
                "ORDER BY generation DESC LIMIT 1",
                (window["id"], stream_id),
            ).fetchone()
            if finalization is None:
                break
            if (
                current_until is not None
                and window["from_index"] != current_until
            ):
                break
            current_until = window["until_index"]
            latest_window_id = window["id"]
            latest_finalization_id = finalization["id"]
        existing = connection.execute(
            "SELECT * FROM crossref_binding_watermarks WHERE stream_id=?",
            (stream_id,),
        ).fetchone()
        if current_until is None:
            if existing is not None:
                raise CrossrefRepairError(
                    "crossref_watermark_state_corrupt"
                )
            return
        if (
            existing is not None
            and Rules.parse_instant(current_until)
            < Rules.parse_instant(existing["finalized_until"])
        ):
            raise CrossrefRepairError("crossref_watermark_state_corrupt")
        assert latest_window_id is not None
        assert latest_finalization_id is not None
        if existing is None:
            connection.execute(
                "INSERT INTO crossref_binding_watermarks("
                "stream_id,binding_key,config_version,rows,finalized_until,"
                "latest_window_id,latest_finalization_id,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    stream_id,
                    values["binding_key"],
                    values["config_version"],
                    values["rows"],
                    current_until,
                    latest_window_id,
                    latest_finalization_id,
                    updated_at,
                ),
            )
        else:
            connection.execute(
                "UPDATE crossref_binding_watermarks SET finalized_until=?,"
                "latest_window_id=?,latest_finalization_id=?,updated_at=? "
                "WHERE stream_id=?",
                (
                    current_until,
                    latest_window_id,
                    latest_finalization_id,
                    updated_at,
                    stream_id,
                ),
            )

    def finalize_window(
        self,
        plan: CrossrefWindowPlan,
        window_id: str,
        *,
        finalized_at: datetime,
        policy: CrossrefRepairPolicy,
    ) -> CrossrefWindowFinalization:
        checked = Rules.policy(policy)
        finalized = Rules.instant(finalized_at)
        stream_id = self.ensure_stream(plan, finalized)
        values = self._plan(plan)
        cutoff = finalized - timedelta(seconds=checked.safety_lag_seconds)
        if plan.definition.until_index.astimezone(cutoff.tzinfo) > cutoff:
            raise CrossrefRepairError(
                "crossref_window_inside_safety_lag"
            )
        with self._transaction(write=True) as connection:
            window = self._require_window(connection, plan, window_id)
            CrossrefWindowTopology.require_leaf(connection, window_id)
            if window["state"] != "traversed":
                raise CrossrefRepairError("crossref_window_not_finalizable")
            running = connection.execute(
                "SELECT 1 FROM crossref_repair_runs "
                "WHERE window_id=? AND state='running' LIMIT 1",
                (window_id,),
            ).fetchone()
            if running is not None:
                raise CrossrefRepairError("crossref_window_not_finalizable")
            pass_row = connection.execute(
                "SELECT * FROM crossref_harvest_passes "
                "WHERE window_id=? ORDER BY pass_no DESC LIMIT 1",
                (window_id,),
            ).fetchone()
            if (
                pass_row is None
                or pass_row["state"] != "completed"
                or not bool(pass_row["traversal_complete"])
                or not bool(pass_row["accounting_complete"])
                or bool(pass_row["repair_pending"])
                or bool(pass_row["drift_suspected"])
                or pass_row["parse_gap_count"] != 0
                or pass_row["source_completeness"] != "provisional"
                or pass_row["finished_at"] is None
            ):
                raise CrossrefRepairError("crossref_window_not_finalizable")
            if Rules.parse_instant(pass_row["finished_at"]) > finalized:
                raise CrossrefRepairError("invalid_crossref_repair_time")
            existing = connection.execute(
                "SELECT * FROM crossref_window_finalizations "
                "WHERE pass_id=?",
                (pass_row["id"],),
            ).fetchone()
            if existing is not None:
                return self._finalization(existing)
            repair = connection.execute(
                "SELECT * FROM crossref_repair_runs "
                "WHERE pass_id=? AND state='completed'",
                (pass_row["id"],),
            ).fetchone()
            reason = "initial" if repair is None else "repair:" + repair["reason"]
            generation = connection.execute(
                "SELECT COALESCE(MAX(generation),0)+1 "
                "FROM crossref_window_finalizations WHERE window_id=?",
                (window_id,),
            ).fetchone()[0]
            finalization_id = "crossref-finalization:" + self._hash(
                window_id,
                generation,
                pass_row["id"],
            )
            connection.execute(
                "INSERT INTO crossref_window_finalizations("
                "id,window_id,stream_id,generation,pass_id,reason,finalized_at"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    finalization_id,
                    window_id,
                    stream_id,
                    generation,
                    pass_row["id"],
                    reason,
                    finalized.isoformat(),
                ),
            )
            self._advance_watermark(
                connection,
                stream_id,
                values,
                finalized.isoformat(),
            )
            row = connection.execute(
                "SELECT * FROM crossref_window_finalizations WHERE id=?",
                (finalization_id,),
            ).fetchone()
            assert row is not None
            return self._finalization(row)

    def read_watermark(
        self,
        plan: CrossrefWindowPlan,
    ) -> CrossrefBindingWatermark | None:
        stream_id = Rules.stream_id(plan)
        with self._transaction(write=False) as connection:
            stream = connection.execute(
                "SELECT * FROM crossref_streams WHERE id=?",
                (stream_id,),
            ).fetchone()
            if stream is None:
                return None
            row = connection.execute(
                "SELECT * FROM crossref_binding_watermarks WHERE stream_id=?",
                (stream_id,),
            ).fetchone()
            return None if row is None else self._watermark(row)
