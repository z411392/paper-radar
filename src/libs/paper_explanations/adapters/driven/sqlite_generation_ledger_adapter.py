import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.paper_explanations.domain.services.generation_execution_rules import GenerationExecutionRules
from libs.paper_explanations.dtos.generation_identity import GenerationIdentity
from libs.paper_explanations.dtos.generation_reservation import GenerationReservation
from libs.paper_explanations.dtos.structured_generation_result import GenerationReceipt, StructuredGenerationResult
from libs.paper_explanations.exceptions.generation_ledger_error import GenerationLedgerError
from libs.paper_explanations.ports.generation_output_store_port import GenerationOutputStorePort


class SqliteGenerationLedgerAdapter:
    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        objects: GenerationOutputStorePort,
    ) -> None:
        self._connect = connect
        self._objects = objects

    @staticmethod
    def _time(value: datetime) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise GenerationLedgerError("invalid_generation_time")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError) as exc:
            raise GenerationLedgerError("invalid_generation_time") from exc

    @staticmethod
    def _run_id(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"run:[0-9a-f]{64}", value) is None:
            raise GenerationLedgerError("invalid_generation_run")
        return value

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.in_transaction:
                raise GenerationLedgerError("owned_connection_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except sqlite3.IntegrityError as exc:
            raise GenerationLedgerError("generation_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "generation_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "generation_database_error"
            )
            raise GenerationLedgerError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _where(identity: GenerationIdentity) -> tuple[str, tuple[object, ...]]:
        return (
            "task_kind=? AND provider=? AND model_name=? AND prompt_digest=? AND input_fingerprint=?",
            (
                identity.task_kind,
                identity.gateway,
                identity.requested_model,
                identity.prompt_digest,
                identity.generation_fingerprint,
            ),
        )

    @classmethod
    def _assert_identity(cls, row: sqlite3.Row, identity: GenerationIdentity) -> None:
        if (
            row["task_kind"] != identity.task_kind
            or row["provider"] != identity.gateway
            or row["model_name"] != identity.requested_model
            or row["prompt_digest"] != identity.prompt_digest
            or row["input_fingerprint"] != identity.generation_fingerprint
        ):
            raise GenerationLedgerError("generation_identity_mismatch")

    @staticmethod
    def _budget_used(connection: sqlite3.Connection, period_key: str, currency: str) -> int:
        rows = connection.execute(
            "SELECT reserved_micros,actual_micros,state FROM usage_reservations "
            "WHERE period_key=? AND currency=?",
            (period_key, currency),
        ).fetchall()
        total = 0
        for row in rows:
            state = row["state"]
            if state == "released":
                continue
            if state == "settled":
                if row["actual_micros"] is None:
                    raise GenerationLedgerError("generation_ledger_corrupt")
                total += row["actual_micros"]
            elif state in {"reserved", "unknown"}:
                total += row["reserved_micros"]
            else:
                raise GenerationLedgerError("generation_ledger_corrupt")
            if total >= 2**63:
                raise GenerationLedgerError("generation_ledger_corrupt")
        return total

    @staticmethod
    def _attempt_id(connection: sqlite3.Connection, identity: GenerationIdentity) -> str:
        where, params = SqliteGenerationLedgerAdapter._where(identity)
        count = connection.execute(f"SELECT COUNT(*) AS n FROM model_runs WHERE {where}", params).fetchone()["n"]
        raw = f"{identity.generation_fingerprint}:{count + 1}".encode("utf-8")
        return "run:" + hashlib.sha256(raw).hexdigest()

    def reserve(self, identity: GenerationIdentity, started_at: datetime) -> GenerationReservation:
        GenerationExecutionRules.validate_identity(identity)
        started = self._time(started_at)
        where, params = self._where(identity)
        with self._transaction(write=True) as connection:
            cached = connection.execute(
                f"SELECT id,output_object_id FROM model_runs WHERE {where} AND state='succeeded' "
                "ORDER BY finished_at DESC,id DESC LIMIT 2",
                params,
            ).fetchall()
            if len(cached) > 1:
                raise GenerationLedgerError("generation_ledger_corrupt")
            if cached:
                if cached[0]["output_object_id"] is None:
                    raise GenerationLedgerError("generation_ledger_corrupt")
                return GenerationReservation("cached", cached[0]["id"], cached[0]["output_object_id"])

            active = connection.execute(
                f"SELECT id FROM model_runs WHERE {where} AND state IN ('reserved','running') "
                "ORDER BY started_at,id LIMIT 2",
                params,
            ).fetchall()
            if len(active) > 1:
                raise GenerationLedgerError("generation_ledger_corrupt")
            if active:
                return GenerationReservation("in_progress", active[0]["id"], None)

            run_id = self._attempt_id(connection, identity)
            used = self._budget_used(connection, identity.period_key, identity.currency)
            if used + identity.reservation_micros > identity.period_limit_micros:
                connection.execute(
                    "INSERT INTO model_runs("
                    "id,task_kind,provider,model_name,model_revision,prompt_digest,input_fingerprint,state,"
                    "output_object_id,input_tokens,output_tokens,actual_cost_micros,error_code,started_at,finished_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        identity.task_kind,
                        identity.gateway,
                        identity.requested_model,
                        None,
                        identity.prompt_digest,
                        identity.generation_fingerprint,
                        "budget_blocked",
                        None,
                        None,
                        None,
                        None,
                        "budget_blocked",
                        started,
                        started,
                    ),
                )
                return GenerationReservation("budget_blocked", run_id, None)

            connection.execute(
                "INSERT INTO model_runs("
                "id,task_kind,provider,model_name,model_revision,prompt_digest,input_fingerprint,state,started_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    identity.task_kind,
                    identity.gateway,
                    identity.requested_model,
                    None,
                    identity.prompt_digest,
                    identity.generation_fingerprint,
                    "reserved",
                    started,
                ),
            )
            reservation_id = "usage:" + hashlib.sha256(
                f"{run_id}:{identity.period_key}:{identity.currency}".encode("utf-8")
            ).hexdigest()
            connection.execute(
                "INSERT INTO usage_reservations("
                "id,run_id,period_key,currency,reserved_micros,actual_micros,state,created_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    reservation_id,
                    run_id,
                    identity.period_key,
                    identity.currency,
                    identity.reservation_micros,
                    None,
                    "reserved",
                    started,
                ),
            )
            return GenerationReservation("reserved", run_id, None)

    def read_cached(self, output_object_id: str, identity: GenerationIdentity) -> StructuredGenerationResult:
        GenerationExecutionRules.validate_identity(identity)
        if not isinstance(output_object_id, str) or re.fullmatch(r"model_output:[0-9a-f]{64}", output_object_id) is None:
            raise GenerationLedgerError("invalid_cached_generation")
        return GenerationExecutionRules.deserialize_success(self._objects.read(output_object_id), identity)

    def mark_running(self, run_id: str, identity: GenerationIdentity) -> None:
        GenerationExecutionRules.validate_identity(identity)
        run_id = self._run_id(run_id)
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT * FROM model_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise GenerationLedgerError("generation_run_missing")
            self._assert_identity(row, identity)
            if row["state"] == "running":
                return
            if row["state"] != "reserved":
                raise GenerationLedgerError("generation_state_conflict")
            connection.execute("UPDATE model_runs SET state='running' WHERE id=?", (run_id,))

    def _load_run(self, connection: sqlite3.Connection, run_id: str, identity: GenerationIdentity) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM model_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise GenerationLedgerError("generation_run_missing")
        self._assert_identity(row, identity)
        return row

    @staticmethod
    def _validate_receipt_identity(receipt: GenerationReceipt, identity: GenerationIdentity) -> None:
        if (
            receipt.input_fingerprint != identity.request_input_fingerprint
            or receipt.requested_model != identity.requested_model
            or receipt.returned_model not in {None, identity.requested_model}
        ):
            raise GenerationLedgerError("generation_receipt_mismatch")

    def complete_success(
        self,
        run_id: str,
        identity: GenerationIdentity,
        result: StructuredGenerationResult,
        finished_at: datetime,
    ) -> None:
        GenerationExecutionRules.validate_identity(identity)
        run_id = self._run_id(run_id)
        finished = self._time(finished_at)
        self._validate_receipt_identity(result.receipt, identity)
        payload = GenerationExecutionRules.serialize_success(result, identity)
        object_id = self._objects.publish(payload)
        actual = (
            None if result.receipt.cost_usd is None else GenerationExecutionRules.cost_micros(result.receipt.cost_usd)
        )
        with self._transaction(write=True) as connection:
            row = self._load_run(connection, run_id, identity)
            if row["state"] == "succeeded":
                if row["output_object_id"] != object_id:
                    raise GenerationLedgerError("generation_state_conflict")
                return
            if row["state"] != "running":
                raise GenerationLedgerError("generation_state_conflict")
            connection.execute(
                "UPDATE model_runs SET state='succeeded',output_object_id=?,input_tokens=?,output_tokens=?,"
                "actual_cost_micros=?,error_code=NULL,finished_at=? WHERE id=?",
                (
                    object_id,
                    result.receipt.input_tokens,
                    result.receipt.output_tokens,
                    actual,
                    finished,
                    run_id,
                ),
            )
            if actual is None:
                connection.execute(
                    "UPDATE usage_reservations SET state='unknown',actual_micros=NULL WHERE run_id=?",
                    (run_id,),
                )
            else:
                connection.execute(
                    "UPDATE usage_reservations SET state='settled',actual_micros=? WHERE run_id=?",
                    (actual, run_id),
                )

    def complete_failure(
        self,
        run_id: str,
        identity: GenerationIdentity,
        error_code: str,
        receipt: GenerationReceipt | None,
        *,
        no_charge: bool,
        finished_at: datetime,
    ) -> None:
        GenerationExecutionRules.validate_identity(identity)
        run_id = self._run_id(run_id)
        finished = self._time(finished_at)
        if not isinstance(error_code, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error_code) is None:
            raise GenerationLedgerError("invalid_generation_error")
        object_id = None
        actual = 0 if no_charge else None
        input_tokens = output_tokens = None
        if receipt is not None:
            self._validate_receipt_identity(receipt, identity)
            object_id = self._objects.publish(
                GenerationExecutionRules.serialize_receipt(receipt, identity)
            )
            input_tokens = receipt.input_tokens
            output_tokens = receipt.output_tokens
            if receipt.cost_usd is not None:
                actual = GenerationExecutionRules.cost_micros(receipt.cost_usd)
        state = "budget_blocked" if error_code == "budget_blocked" else "failed"
        with self._transaction(write=True) as connection:
            row = self._load_run(connection, run_id, identity)
            if row["state"] in {"failed", "budget_blocked"}:
                if row["error_code"] != error_code or row["output_object_id"] != object_id:
                    raise GenerationLedgerError("generation_state_conflict")
                return
            if row["state"] != "running":
                raise GenerationLedgerError("generation_state_conflict")
            connection.execute(
                "UPDATE model_runs SET state=?,output_object_id=?,input_tokens=?,output_tokens=?,"
                "actual_cost_micros=?,error_code=?,finished_at=? WHERE id=?",
                (state, object_id, input_tokens, output_tokens, actual, error_code, finished, run_id),
            )
            if actual is not None:
                reservation_state = "released" if no_charge and actual == 0 else "settled"
                connection.execute(
                    "UPDATE usage_reservations SET state=?,actual_micros=? WHERE run_id=?",
                    (reservation_state, actual if reservation_state == "settled" else None, run_id),
                )
            else:
                connection.execute(
                    "UPDATE usage_reservations SET state='unknown',actual_micros=NULL WHERE run_id=?",
                    (run_id,),
                )

    def reconcile_stale(self, before: datetime, reconciled_at: datetime) -> tuple[int, int]:
        cutoff = self._time(before)
        reconciled = self._time(reconciled_at)
        released = 0
        unknown = 0
        with self._transaction(write=True) as connection:
            rows = connection.execute(
                "SELECT id,state FROM model_runs WHERE state IN ('reserved','running') AND started_at<? "
                "ORDER BY started_at,id",
                (cutoff,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE model_runs SET state='stale',error_code='stale_attempt',finished_at=? WHERE id=?",
                    (reconciled, row["id"]),
                )
                connection.execute(
                    "UPDATE usage_reservations SET state='unknown',actual_micros=NULL WHERE run_id=?",
                    (row["id"],),
                )
                unknown += 1
        return released, unknown
