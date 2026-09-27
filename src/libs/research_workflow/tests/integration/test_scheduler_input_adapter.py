import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from libs.research_workflow.adapters.driven.sqlite_scheduler_input_adapter import (
    SqliteSchedulerInputAdapter,
)
from libs.research_workflow.domain.services.plan_catchup_jobs import PlanCatchupJobs


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    return factory


def _setup(tmp_path: Path) -> tuple[Path, SqliteSchedulerInputAdapter]:
    path = tmp_path / "scheduler.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE watch_profiles(
          id TEXT PRIMARY KEY,reader_id TEXT NOT NULL,name TEXT NOT NULL,lifecycle TEXT NOT NULL,
          published_revision INTEGER,created_at TEXT NOT NULL);
        CREATE TABLE watch_profile_revisions(
          profile_id TEXT NOT NULL,revision INTEGER NOT NULL,scope_text TEXT NOT NULL,
          filters_json TEXT NOT NULL,fingerprint TEXT NOT NULL,published_at TEXT NOT NULL,
          PRIMARY KEY(profile_id,revision));
        CREATE TABLE watch_profile_domains(
          profile_id TEXT NOT NULL,revision INTEGER NOT NULL,domain_id TEXT NOT NULL,
          domain_revision INTEGER NOT NULL,PRIMARY KEY(profile_id,revision,domain_id));
        CREATE TABLE domain_definitions(
          id TEXT NOT NULL,name TEXT NOT NULL,definition_json TEXT NOT NULL,
          revision INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(id,revision));
        CREATE TABLE delivery_subscriptions(
          id TEXT PRIMARY KEY,reader_id TEXT NOT NULL,channel TEXT NOT NULL,
          enabled INTEGER NOT NULL,timezone TEXT NOT NULL,schedule_json TEXT NOT NULL,
          max_items INTEGER NOT NULL,recipient_ref TEXT NOT NULL,policy_version INTEGER NOT NULL,
          created_at TEXT NOT NULL);
        CREATE TABLE digests(
          id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL,
          period_key TEXT NOT NULL,state TEXT NOT NULL);
        CREATE TABLE delivery_outbox(
          id TEXT PRIMARY KEY,digest_id TEXT NOT NULL,state TEXT NOT NULL);
        CREATE TABLE workflow_jobs(
          id TEXT PRIMARY KEY,job_kind TEXT NOT NULL,business_key TEXT NOT NULL UNIQUE,
          input_json TEXT NOT NULL,input_fingerprint TEXT NOT NULL,state TEXT NOT NULL,
          due_at TEXT NOT NULL,lease_owner TEXT,lease_until TEXT,fencing_token INTEGER NOT NULL,
          attempt_count INTEGER NOT NULL,created_at TEXT NOT NULL);
        """
    )
    connection.execute(
        "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
        ("personal", "local", "mine", "active", 3, NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
        (
            "personal",
            3,
            "scope",
            '{"allow_preprints":true,"exclude":[],"free_only":true,"include":[],'
            '"languages":["en"],"sources":["arxiv","crossref","pubmed"]}',
            "a" * 64,
            NOW.isoformat(),
        ),
    )
    for domain_id, definition in (
        (
            "statistics",
            '{"aliases":[],"exclude":[],"include":["statistics"],'
            '"source_categories":{"arxiv":["stat.ML"]},'
            '"sources":["arxiv","crossref"]}',
        ),
        (
            "badminton",
            '{"aliases":[],"exclude":[],"include":["badminton"],'
            '"source_categories":{},"sources":["pubmed","crossref"]}',
        ),
    ):
        connection.execute(
            "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
            (domain_id, domain_id, definition, 1, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
            ("personal", 3, domain_id, 1),
        )
    connection.execute(
        "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "subscription:daily",
            "local",
            "email",
            1,
            "Asia/Taipei",
            '{"kind":"daily","local_time":"08:00"}',
            5,
            "recipient:primary",
            1,
            NOW.isoformat(),
        ),
    )
    connection.commit()
    connection.close()
    return path, SqliteSchedulerInputAdapter(_connect(path))


def test_reads_active_profile_source_intersection_and_delivery_schedule(tmp_path: Path) -> None:
    _, adapter = _setup(tmp_path)

    snapshot = adapter.read(NOW)

    assert {
        (item.domain_id, item.source_id) for item in snapshot.harvest_bindings
    } == {
        ("statistics", "arxiv"),
        ("statistics", "crossref"),
        ("badminton", "pubmed"),
        ("badminton", "crossref"),
    }
    assert len(snapshot.delivery_schedules) == 1
    schedule = snapshot.delivery_schedules[0]
    assert schedule.subscription_id == "subscription:daily"
    assert schedule.timezone == "Asia/Taipei"
    assert schedule.local_time == "08:00"
    assert snapshot.input_gaps == ()


def test_only_succeeded_harvest_job_advances_binding_cursor(tmp_path: Path) -> None:
    path, adapter = _setup(tmp_path)
    first = NOW.replace(day=22)
    second = NOW.replace(day=23)
    binding_key = "personal:3:statistics:1:arxiv"
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO workflow_jobs VALUES(?,?,?,?,?,'succeeded',?,NULL,NULL,1,1,?)",
        (
            "job:success",
            "harvest_window",
            "harvest:success",
            '{"binding_key":"' + binding_key + '","window_end":"' + first.isoformat() + '"}',
            "b" * 64,
            first.isoformat(),
            first.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO workflow_jobs VALUES(?,?,?,?,?,'failed',?,NULL,NULL,2,2,?)",
        (
            "job:failed",
            "harvest_window",
            "harvest:failed",
            '{"binding_key":"' + binding_key + '","window_end":"' + second.isoformat() + '"}',
            "c" * 64,
            second.isoformat(),
            second.isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    snapshot = adapter.read(NOW)
    item = next(
        value
        for value in snapshot.harvest_bindings
        if value.domain_id == "statistics" and value.source_id == "arxiv"
    )

    assert item.last_succeeded_window_end == first
    assert {job.state for job in snapshot.known_jobs} == {"succeeded", "failed"}


def test_last_scheduled_digest_cutoff_includes_failed_job_to_prevent_old_daily_rebuild(
    tmp_path: Path,
) -> None:
    path, adapter = _setup(tmp_path)
    cutoff = NOW.replace(day=23)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO workflow_jobs VALUES(?,?,?,?,?,'failed',?,NULL,NULL,1,1,?)",
        (
            "job:digest",
            "prepare_digest",
            "digest:subscription:daily:2026-09-23",
            '{"subscription_id":"subscription:daily","cutoff_at":"' + cutoff.isoformat() + '"}',
            "d" * 64,
            cutoff.isoformat(),
            cutoff.isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    snapshot = adapter.read(NOW)

    assert snapshot.delivery_schedules[0].last_scheduled_cutoff == cutoff


def test_invalid_or_duplicate_schedule_json_becomes_visible_gap_not_guessed_time(
    tmp_path: Path,
) -> None:
    path, adapter = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE delivery_subscriptions SET schedule_json=? WHERE id='subscription:daily'",
        ('{"kind":"daily","local_time":"08:00","local_time":"09:00"}',),
    )
    connection.commit()
    connection.close()

    snapshot = adapter.read(NOW)

    assert snapshot.delivery_schedules == ()
    assert snapshot.input_gaps == (
        adapter.gap("delivery", "subscription:daily", "invalid_delivery_schedule"),
    )


def test_pending_queued_outbox_is_exposed_to_scheduler(tmp_path: Path) -> None:
    path, adapter = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO digests VALUES(?,?,?,'queued')",
        ("digest:pending", "subscription:daily", "2026-09-24"),
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES(?,?,'pending')",
        ("outbox:pending", "digest:pending"),
    )
    connection.execute(
        "INSERT INTO digests VALUES(?,?,?,'sent')",
        ("digest:sent", "subscription:daily", "2026-09-23"),
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES(?,?,'provider_accepted')",
        ("outbox:sent", "digest:sent"),
    )
    connection.commit()
    connection.close()

    snapshot = adapter.read(NOW)

    assert snapshot.pending_delivery_outboxes == ("outbox:pending",)


def test_pending_explanation_defers_due_daily_digest(tmp_path: Path) -> None:
    path, adapter = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE watch_profiles SET lifecycle='paused' WHERE id='personal'"
    )
    connection.execute(
        "INSERT INTO workflow_jobs VALUES(?,?,?,?,?,'pending',?,NULL,NULL,0,0,?)",
        (
            "job:explain",
            "explain_snapshot",
            "explain:snapshot:pending",
            '{"snapshot_id":"snapshot:pending"}',
            "e" * 64,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    snapshot = adapter.read(NOW)
    plan = PlanCatchupJobs()(snapshot, now=NOW)

    assert plan.digest_deferred is True
    assert all(job.job_kind != "prepare_digest" for job in plan.jobs)


def _plan_with_explanation_state(
    tmp_path: Path,
    state: str,
):
    path, adapter = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE watch_profiles SET lifecycle='paused' WHERE id='personal'"
    )
    connection.execute(
        "INSERT INTO workflow_jobs VALUES(?,?,?,?,?,?,?,NULL,NULL,0,0,?)",
        (
            "job:explain:" + state,
            "explain_snapshot",
            "explain:snapshot:" + state,
            '{"snapshot_id":"snapshot:' + state + '"}',
            "f" * 64,
            state,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    return PlanCatchupJobs()(adapter.read(NOW), now=NOW)


def test_failed_explanation_becomes_coverage_gap_without_blocking_digest(
    tmp_path: Path,
) -> None:
    plan = _plan_with_explanation_state(tmp_path, "failed")

    assert plan.digest_deferred is False
    digest = next(job for job in plan.jobs if job.job_kind == "prepare_digest")
    payload = PlanCatchupJobs.decode(digest.input_json)
    assert payload["coverage_gaps"] == [
        {
            "identity": "explain:snapshot:failed",
            "kind": "explanation",
            "reason": "explanation_failed",
        }
    ]


def test_awaiting_explanation_becomes_coverage_gap_without_blocking_digest(
    tmp_path: Path,
) -> None:
    plan = _plan_with_explanation_state(tmp_path, "awaiting_external")

    assert plan.digest_deferred is False
    digest = next(job for job in plan.jobs if job.job_kind == "prepare_digest")
    payload = PlanCatchupJobs.decode(digest.input_json)
    assert payload["coverage_gaps"] == [
        {
            "identity": "explain:snapshot:awaiting_external",
            "kind": "explanation",
            "reason": "explanation_awaiting_external",
        }
    ]
