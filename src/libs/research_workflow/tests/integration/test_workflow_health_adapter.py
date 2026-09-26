import sqlite3
from pathlib import Path

import pytest

from libs.research_workflow.adapters.driven.sqlite_workflow_health_adapter import (
    SqliteWorkflowHealthAdapter,
)
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
)


ROOT = Path(__file__).resolve().parents[5]


def test_reads_harvest_jobs_and_latest_attempt_error_without_mutation(tmp_path: Path):
    database = tmp_path / "health.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        (ROOT / "migrations/0008-workflow-jobs.sql").read_text(encoding="utf-8")
    )
    connection.execute(
        "INSERT INTO workflow_jobs("
        "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,"
        "fencing_token,attempt_count,created_at"
        ") VALUES(?,?,?,?,?,'failed',?,2,2,?)",
        (
            "job:failed",
            "harvest_window",
            "harvest:pubmed",
            "{}",
            "a" * 64,
            "2026-09-26T07:00:00+00:00",
            "2026-09-26T06:00:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO job_attempts VALUES(?,?,?,?,?,?,?,?)",
        (
            "attempt:1",
            "job:failed",
            1,
            1,
            "failed",
            "old_error",
            "2026-09-26T06:01:00+00:00",
            "2026-09-26T06:02:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO job_attempts VALUES(?,?,?,?,?,?,?,?)",
        (
            "attempt:2",
            "job:failed",
            2,
            2,
            "failed",
            "ncbi_unavailable",
            "2026-09-26T06:03:00+00:00",
            "2026-09-26T06:04:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO workflow_jobs("
        "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,created_at"
        ") VALUES(?,?,?,?,?,'pending',?,?)",
        (
            "job:pending",
            "harvest_window",
            "harvest:crossref",
            "{}",
            "b" * 64,
            "2026-09-26T07:30:00+00:00",
            "2026-09-26T07:15:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO workflow_jobs("
        "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,created_at"
        ") VALUES(?,?,?,?,?,'pending',?,?)",
        (
            "job:digest",
            "prepare_digest",
            "digest:today",
            "{}",
            "c" * 64,
            "2026-09-26T07:30:00+00:00",
            "2026-09-26T07:20:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(database, isolation_level=None)

    rows = SqliteWorkflowHealthAdapter(connect)()

    assert [row.business_key for row in rows] == [
        "harvest:crossref",
        "harvest:pubmed",
    ]
    by_key = {row.business_key: row for row in rows}
    assert by_key["harvest:crossref"].finished_at is None
    assert by_key["harvest:crossref"].last_error_code is None
    assert by_key["harvest:pubmed"].finished_at == "2026-09-26T06:04:00+00:00"
    assert by_key["harvest:pubmed"].last_error_code == "ncbi_unavailable"


def test_succeeded_job_with_failed_latest_attempt_fails_closed(tmp_path: Path):
    database = tmp_path / "mismatch.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        (ROOT / "migrations/0008-workflow-jobs.sql").read_text(encoding="utf-8")
    )
    connection.execute(
        "INSERT INTO workflow_jobs("
        "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,"
        "fencing_token,attempt_count,created_at"
        ") VALUES(?,?,?,?,?,'succeeded',?,1,1,?)",
        (
            "job:mismatch",
            "harvest_window",
            "harvest:mismatch",
            "{}",
            "d" * 64,
            "2026-09-26T07:00:00+00:00",
            "2026-09-26T06:00:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO job_attempts VALUES(?,?,?,?,?,?,?,?)",
        (
            "attempt:mismatch",
            "job:mismatch",
            1,
            1,
            "failed",
            "source_unavailable",
            "2026-09-26T06:01:00+00:00",
            "2026-09-26T06:02:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(database, isolation_level=None)

    with pytest.raises(
        OperationalHealthError,
        match="workflow_health_ledger_mismatch",
    ):
        SqliteWorkflowHealthAdapter(connect)()
