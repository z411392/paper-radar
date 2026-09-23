import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.research_workflow.adapters.driven.sqlite_workflow_job_store_adapter import (
    SqliteWorkflowJobStoreAdapter,
)
from libs.research_workflow.dtos.workflow_job import (
    CompleteWorkflowJob,
    EnqueueWorkflowJob,
)
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path) -> tuple[Path, SqliteWorkflowJobStoreAdapter]:
    path = tmp_path / "workflow.sqlite3"
    connection = sqlite3.connect(path)
    migration = (
        Path(__file__).resolve().parents[5] / "migrations" / "0008-workflow-jobs.sql"
    ).read_text(encoding="utf-8")
    connection.executescript(migration)
    connection.close()
    return path, SqliteWorkflowJobStoreAdapter(_connect(path))


def _job(
    *,
    business_key: str = "harvest:arxiv:binding-a:2026-09-23",
    fingerprint: str = "a" * 64,
    due_at: datetime = NOW,
) -> EnqueueWorkflowJob:
    return EnqueueWorkflowJob(
        job_kind="harvest_window",
        business_key=business_key,
        input_json='{"binding":"binding-a","window":"2026-09-23"}',
        input_fingerprint=fingerprint,
        due_at=due_at,
        created_at=NOW - timedelta(minutes=1),
    )


def test_enqueue_is_replayable_but_conflicting_business_identity_is_rejected(
    tmp_path: Path,
) -> None:
    path, store = _setup(tmp_path)

    first = store.enqueue(_job())
    replay = store.enqueue(_job())

    assert replay.replayed is True
    assert replay.job_id == first.job_id
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM workflow_jobs").fetchone()[0] == 1
    connection.close()

    with pytest.raises(WorkflowJobError, match="job_identity_conflict"):
        store.enqueue(_job(fingerprint="b" * 64))


def test_only_one_owner_can_claim_an_unexpired_lease(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    store.enqueue(_job())

    first = store.claim_due("worker:a", now=NOW, lease_seconds=60)
    second = store.claim_due("worker:b", now=NOW, lease_seconds=60)

    assert first is not None
    assert first.owner_id == "worker:a"
    assert first.fencing_token == 1
    assert first.attempt_no == 1
    assert second is None

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT state,lease_owner,fencing_token,attempt_count FROM workflow_jobs"
    ).fetchone() == ("running", "worker:a", 1, 1)
    assert connection.execute(
        "SELECT state,fencing_token FROM job_attempts"
    ).fetchone() == ("running", 1)
    connection.close()


def test_expired_lease_is_reclaimed_with_higher_token_and_old_owner_cannot_commit(
    tmp_path: Path,
) -> None:
    path, store = _setup(tmp_path)
    store.enqueue(_job())
    old = store.claim_due("worker:old", now=NOW, lease_seconds=10)
    assert old is not None

    new = store.claim_due(
        "worker:new",
        now=NOW + timedelta(seconds=11),
        lease_seconds=60,
    )

    assert new is not None
    assert new.job_id == old.job_id
    assert new.fencing_token == 2
    assert new.attempt_no == 2

    with pytest.raises(WorkflowJobError, match="fencing_mismatch"):
        store.complete(
            CompleteWorkflowJob(
                job_id=old.job_id,
                owner_id=old.owner_id,
                fencing_token=old.fencing_token,
                state="succeeded",
                error_code=None,
                finished_at=NOW + timedelta(seconds=12),
                next_due_at=None,
            )
        )

    connection = sqlite3.connect(path)
    attempts = connection.execute(
        "SELECT attempt_no,fencing_token,state,error_code FROM job_attempts ORDER BY attempt_no"
    ).fetchall()
    assert attempts == [
        (1, 1, "failed", "lease_expired"),
        (2, 2, "running", None),
    ]
    connection.close()


def test_expired_owner_cannot_commit_even_before_another_worker_reclaims(
    tmp_path: Path,
) -> None:
    _, store = _setup(tmp_path)
    store.enqueue(_job())
    lease = store.claim_due("worker:a", now=NOW, lease_seconds=10)
    assert lease is not None

    with pytest.raises(WorkflowJobError, match="lease_expired"):
        store.complete(
            CompleteWorkflowJob(
                job_id=lease.job_id,
                owner_id=lease.owner_id,
                fencing_token=lease.fencing_token,
                state="succeeded",
                error_code=None,
                finished_at=NOW + timedelta(seconds=10),
                next_due_at=None,
            )
        )


def test_success_completion_is_replayable_until_a_new_attempt_exists(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    store.enqueue(_job())
    lease = store.claim_due("worker:a", now=NOW, lease_seconds=60)
    assert lease is not None
    completion = CompleteWorkflowJob(
        job_id=lease.job_id,
        owner_id=lease.owner_id,
        fencing_token=lease.fencing_token,
        state="succeeded",
        error_code=None,
        finished_at=NOW + timedelta(seconds=5),
        next_due_at=None,
    )

    first = store.complete(completion)
    replay = store.complete(completion)

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.state == "succeeded"
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM job_attempts").fetchone()[0] == 1
    assert connection.execute("SELECT state FROM workflow_jobs").fetchone()[0] == "succeeded"
    connection.close()


def test_failed_job_waits_until_next_due_before_getting_a_new_fencing_token(
    tmp_path: Path,
) -> None:
    _, store = _setup(tmp_path)
    store.enqueue(_job())
    lease = store.claim_due("worker:a", now=NOW, lease_seconds=60)
    assert lease is not None
    retry_at = NOW + timedelta(minutes=5)

    store.complete(
        CompleteWorkflowJob(
            job_id=lease.job_id,
            owner_id=lease.owner_id,
            fencing_token=lease.fencing_token,
            state="failed",
            error_code="source_timeout",
            finished_at=NOW + timedelta(seconds=5),
            next_due_at=retry_at,
        )
    )

    assert store.claim_due(
        "worker:b",
        now=retry_at - timedelta(seconds=1),
        lease_seconds=60,
    ) is None
    retried = store.claim_due("worker:b", now=retry_at, lease_seconds=60)

    assert retried is not None
    assert retried.fencing_token == 2
    assert retried.attempt_no == 2


def test_two_concurrent_claimers_produce_one_active_attempt(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    store.enqueue(_job())
    gate = threading.Barrier(2)
    results = []
    errors = []

    def worker(owner: str) -> None:
        try:
            gate.wait(timeout=2)
            results.append(store.claim_due(owner, now=NOW, lease_seconds=60))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("worker:a",)),
        threading.Thread(target=worker, args=("worker:b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert sum(item is not None for item in results) == 1
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM job_attempts").fetchone()[0] == 1
    assert connection.execute(
        "SELECT count(*) FROM workflow_jobs WHERE state='running'"
    ).fetchone()[0] == 1
    connection.close()
