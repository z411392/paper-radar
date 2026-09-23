import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from libs.research_workflow.domain.services.workflow_job_rules import WorkflowJobRules
from libs.research_workflow.dtos.workflow_job import (
    CompleteWorkflowJob,
    EnqueuedWorkflowJob,
    EnqueueWorkflowJob,
    WorkflowJobCompletion,
    WorkflowJobLease,
)
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError


class SqliteWorkflowJobStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.in_transaction:
                raise WorkflowJobError("owned_connection_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except WorkflowJobError:
            raise
        except sqlite3.IntegrityError as exc:
            raise WorkflowJobError("workflow_job_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "workflow_job_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "workflow_job_database_error"
            )
            raise WorkflowJobError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    def enqueue(self, request: EnqueueWorkflowJob) -> EnqueuedWorkflowJob:
        checked = WorkflowJobRules.enqueue(request)
        job_id = WorkflowJobRules.job_id(checked.business_key)
        due_at = WorkflowJobRules.instant_text(checked.due_at)
        created_at = WorkflowJobRules.instant_text(checked.created_at)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_jobs WHERE business_key=?",
                (checked.business_key,),
            ).fetchone()
            if row is not None:
                expected = (
                    job_id,
                    checked.job_kind,
                    checked.input_json,
                    checked.input_fingerprint,
                    due_at,
                )
                actual = (
                    row["id"],
                    row["job_kind"],
                    row["input_json"],
                    row["input_fingerprint"],
                    row["due_at"],
                )
                if actual != expected:
                    raise WorkflowJobError("job_identity_conflict")
                return EnqueuedWorkflowJob(job_id, True)

            connection.execute(
                "INSERT INTO workflow_jobs("
                "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,created_at"
                ") VALUES(?,?,?,?,?,'pending',?,?)",
                (
                    job_id,
                    checked.job_kind,
                    checked.business_key,
                    checked.input_json,
                    checked.input_fingerprint,
                    due_at,
                    created_at,
                ),
            )
            return EnqueuedWorkflowJob(job_id, False)

    @staticmethod
    def _claimable_row(
        connection: sqlite3.Connection,
        now_text: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM workflow_jobs "
            "WHERE due_at<=? AND ("
            "state IN ('pending','failed','awaiting_external') OR "
            "(state='running' AND lease_until IS NOT NULL AND lease_until<=?)"
            ") ORDER BY due_at,id LIMIT 1",
            (now_text, now_text),
        ).fetchone()

    @staticmethod
    def _expire_previous_attempt(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now_text: str,
    ) -> None:
        if row["state"] != "running":
            return
        changed = connection.execute(
            "UPDATE job_attempts SET state='failed',error_code='lease_expired',finished_at=? "
            "WHERE job_id=? AND attempt_no=? AND fencing_token=? AND state='running'",
            (
                now_text,
                row["id"],
                row["attempt_count"],
                row["fencing_token"],
            ),
        ).rowcount
        if changed != 1:
            raise WorkflowJobError("workflow_job_corrupt")

    def claim_due(
        self,
        owner_id: str,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> WorkflowJobLease | None:
        owner = WorkflowJobRules.owner(owner_id)
        current = WorkflowJobRules.instant(now, "invalid_claim_time")
        seconds = WorkflowJobRules.lease_seconds(lease_seconds)
        now_text = WorkflowJobRules.instant_text(current)
        lease_until = current + timedelta(seconds=seconds)
        lease_text = WorkflowJobRules.instant_text(lease_until)

        with self._transaction() as connection:
            row = self._claimable_row(connection, now_text)
            if row is None:
                return None
            self._expire_previous_attempt(connection, row, now_text)

            token = row["fencing_token"] + 1
            attempt_no = row["attempt_count"] + 1
            attempt_id = WorkflowJobRules.attempt_id(row["id"], attempt_no, token)
            changed = connection.execute(
                "UPDATE workflow_jobs SET state='running',lease_owner=?,lease_until=?,"
                "fencing_token=?,attempt_count=? WHERE id=? AND fencing_token=?",
                (
                    owner,
                    lease_text,
                    token,
                    attempt_no,
                    row["id"],
                    row["fencing_token"],
                ),
            ).rowcount
            if changed != 1:
                raise WorkflowJobError("workflow_job_claim_conflict")
            connection.execute(
                "INSERT INTO job_attempts("
                "id,job_id,attempt_no,fencing_token,state,started_at"
                ") VALUES(?,?,?,?, 'running',?)",
                (attempt_id, row["id"], attempt_no, token, now_text),
            )
            return WorkflowJobLease(
                job_id=row["id"],
                job_kind=row["job_kind"],
                business_key=row["business_key"],
                input_json=row["input_json"],
                input_fingerprint=row["input_fingerprint"],
                owner_id=owner,
                fencing_token=token,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                lease_until=lease_until,
            )

    @staticmethod
    def _completion_replay(
        connection: sqlite3.Connection,
        request: CompleteWorkflowJob,
        row: sqlite3.Row,
    ) -> WorkflowJobCompletion | None:
        if (
            row["fencing_token"] != request.fencing_token
            or row["lease_owner"] != request.owner_id
            or row["state"] != request.state
        ):
            return None
        attempt = connection.execute(
            "SELECT state,error_code,finished_at FROM job_attempts "
            "WHERE job_id=? AND attempt_no=? AND fencing_token=?",
            (row["id"], row["attempt_count"], row["fencing_token"]),
        ).fetchone()
        if attempt is None:
            raise WorkflowJobError("workflow_job_corrupt")
        expected_error = request.error_code
        if (
            attempt["state"] == request.state
            and attempt["error_code"] == expected_error
            and attempt["finished_at"] == WorkflowJobRules.instant_text(request.finished_at)
        ):
            return WorkflowJobCompletion(row["id"], row["state"], row["fencing_token"], True)
        return None

    def complete(self, request: CompleteWorkflowJob) -> WorkflowJobCompletion:
        checked = WorkflowJobRules.completion(request)
        finished = WorkflowJobRules.instant_text(checked.finished_at)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_jobs WHERE id=?",
                (checked.job_id,),
            ).fetchone()
            if row is None:
                raise WorkflowJobError("workflow_job_missing")

            if row["state"] != "running":
                replay = self._completion_replay(connection, checked, row)
                if replay is not None:
                    return replay
                raise WorkflowJobError("fencing_mismatch")
            if (
                row["lease_owner"] != checked.owner_id
                or row["fencing_token"] != checked.fencing_token
            ):
                raise WorkflowJobError("fencing_mismatch")
            if row["lease_until"] is None or row["lease_until"] <= finished:
                raise WorkflowJobError("lease_expired")

            next_due = (
                WorkflowJobRules.instant_text(checked.next_due_at)
                if checked.next_due_at is not None
                else row["due_at"]
            )
            changed = connection.execute(
                "UPDATE workflow_jobs SET state=?,due_at=?,lease_until=NULL "
                "WHERE id=? AND state='running' AND lease_owner=? AND fencing_token=?",
                (
                    checked.state,
                    next_due,
                    checked.job_id,
                    checked.owner_id,
                    checked.fencing_token,
                ),
            ).rowcount
            if changed != 1:
                raise WorkflowJobError("fencing_mismatch")
            attempt = connection.execute(
                "UPDATE job_attempts SET state=?,error_code=?,finished_at=? "
                "WHERE job_id=? AND attempt_no=? AND fencing_token=? AND state='running'",
                (
                    checked.state,
                    checked.error_code,
                    finished,
                    checked.job_id,
                    row["attempt_count"],
                    checked.fencing_token,
                ),
            ).rowcount
            if attempt != 1:
                raise WorkflowJobError("workflow_job_corrupt")
            return WorkflowJobCompletion(
                checked.job_id,
                checked.state,
                checked.fencing_token,
                False,
            )
