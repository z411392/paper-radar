import sqlite3
from collections.abc import Callable

from libs.research_workflow.dtos.operational_health import WorkflowHealthEvidence
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
)


_TERMINAL_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "awaiting_external", "budget_blocked"}
)


class SqliteWorkflowHealthAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _reconcile(row: sqlite3.Row) -> None:
        job_state = row["state"]
        attempt_state = row["latest_attempt_state"]
        finished_at = row["finished_at"]
        error_code = row["last_error_code"]

        if job_state == "pending":
            if attempt_state is not None or finished_at is not None or error_code is not None:
                raise OperationalHealthError("workflow_health_ledger_mismatch")
            return
        if job_state == "running":
            if (
                attempt_state != "running"
                or finished_at is not None
                or error_code is not None
            ):
                raise OperationalHealthError("workflow_health_ledger_mismatch")
            return
        if job_state in _TERMINAL_STATES:
            if attempt_state != job_state or finished_at is None:
                raise OperationalHealthError("workflow_health_ledger_mismatch")
            if job_state == "succeeded" and error_code is not None:
                raise OperationalHealthError("workflow_health_ledger_mismatch")
            return
        raise OperationalHealthError("workflow_health_corrupt")

    def __call__(self) -> tuple[WorkflowHealthEvidence, ...]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT j.business_key,j.state,j.created_at,j.due_at,"
                "(SELECT a.state FROM job_attempts a "
                "WHERE a.job_id=j.id ORDER BY a.attempt_no DESC LIMIT 1) "
                "AS latest_attempt_state,"
                "(SELECT a.finished_at FROM job_attempts a "
                "WHERE a.job_id=j.id ORDER BY a.attempt_no DESC LIMIT 1) AS finished_at,"
                "(SELECT a.error_code FROM job_attempts a "
                "WHERE a.job_id=j.id ORDER BY a.attempt_no DESC LIMIT 1) AS last_error_code "
                "FROM workflow_jobs j WHERE j.job_kind='harvest_window' "
                "ORDER BY j.business_key"
            ).fetchall()
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise OperationalHealthError("workflow_health_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        result = []
        for row in rows:
            if not all(
                isinstance(row[key], str) and row[key]
                for key in ("business_key", "state", "created_at", "due_at")
            ):
                raise OperationalHealthError("workflow_health_corrupt")
            for optional in (
                "latest_attempt_state",
                "finished_at",
                "last_error_code",
            ):
                if row[optional] is not None and not isinstance(row[optional], str):
                    raise OperationalHealthError("workflow_health_corrupt")
            self._reconcile(row)
            result.append(
                WorkflowHealthEvidence(
                    business_key=row["business_key"],
                    state=row["state"],
                    created_at=row["created_at"],
                    due_at=row["due_at"],
                    finished_at=row["finished_at"],
                    last_error_code=row["last_error_code"],
                )
            )
        return tuple(result)
