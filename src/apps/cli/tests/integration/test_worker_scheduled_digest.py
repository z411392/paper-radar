import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from libs.delivery.adapters.driven.kernel_digest_artifact_adapter import KernelDigestArtifactAdapter
from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import SqliteDeliveryStoreAdapter
from libs.delivery.adapters.driven.sqlite_digest_delivery_context_adapter import (
    SqliteDigestDeliveryContextAdapter,
)
from libs.delivery.application.commands.prepare_scheduled_digest import PrepareScheduledDigest
from libs.delivery.application.commands.queue_digest import QueueDigest
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.paper_explanations.adapters.driven.sqlite_digest_current_summary_adapter import (
    SqliteDigestCurrentSummaryAdapter,
)
from libs.research_workflow.adapters.driven.sqlite_scheduler_input_adapter import (
    SqliteSchedulerInputAdapter,
)
from libs.research_workflow.adapters.driven.sqlite_workflow_job_store_adapter import (
    SqliteWorkflowJobStoreAdapter,
)
from libs.research_workflow.application.commands.process_workflow_job import ProcessWorkflowJob
from libs.research_workflow.application.commands.run_scheduler_tick import RunSchedulerTick
from libs.research_workflow.application.commands.run_worker_cycle import RunWorkerCycle
from libs.scholarly_catalog.adapters.driven.sqlite_digest_research_event_adapter import (
    SqliteDigestResearchEventAdapter,
)
from libs.watch_profiles.adapters.driven.sqlite_digest_relevance_adapter import (
    SqliteDigestRelevanceAdapter,
)


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
WORK = "work:runtime"
REVISION = "revision:runtime"
SNAPSHOT = "snapshot:runtime"
SUMMARY = "summary:runtime"
GENERATION = "a" * 64
RUN = "run:runtime"


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _summary_artifact() -> bytes:
    return json.dumps(
        {
            "format_version": 1,
            "snapshot_id": SNAPSHOT,
            "revision_id": REVISION,
            "work_id": WORK,
            "generation_fingerprint": GENERATION,
            "generation_input_fingerprint": "b" * 64,
            "language": "zh-TW",
            "explanation_profile": "plain-zh-TW-v1",
            "evidence_level": "abstract_only",
            "original_abstract": "Original abstract.",
            "faithful_translation": [],
            "plain_language_card": [
                {
                    "claim_type": "result",
                    "text": "這篇研究以白話整理出主要結果。",
                    "claim_ids": ["claim:runtime"],
                }
            ],
            "not_reported_in_read_evidence": [],
            "claim_ids": ["claim:runtime"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _seed_workspace(root: Path) -> SqliteSchemaConnectionFactory:
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 9
    assert info.external_effects_enabled is False

    raw = SqliteConnectionFactory(root)
    files = FilesystemObjectBytesAdapter(root)
    objects = SqliteObjectUnitOfWorkAdapter(raw)
    publish = PublishObject(files, objects)
    evidence = publish(b"evidence", "evidence", "text/plain", "test")
    extracted = publish(b"abstract", "extracted", "text/plain", "test")
    summary = publish(
        _summary_artifact(),
        "model_output",
        "application/json; charset=utf-8",
        "verified-explanation-v1",
    )

    connection = raw.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
            (
                "badminton",
                "羽球",
                '{"aliases":["badminton"],"exclude":[],"include":["badminton"],'
                '"source_categories":{},"sources":["pubmed"]}',
                1,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
            ("personal", "reader:local", "我的研究", "active", None, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
            (
                "personal",
                1,
                "羽球研究",
                '{"allow_preprints":true,"exclude":[],"free_only":true,"include":[],'
                '"languages":["en"],"sources":["pubmed"]}',
                "c" * 64,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
            ("personal", 1, "badminton", 1),
        )
        connection.execute(
            "UPDATE watch_profiles SET published_revision=1 WHERE id='personal'"
        )

        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "Badminton paper",
                "published",
                "2026-09-23",
                "day",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
            (
                "manifest:runtime",
                WORK,
                "pubmed",
                "12345678",
                "publication",
                "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                REVISION,
                "manifest:runtime",
                WORK,
                "1",
                "d" * 64,
                "Badminton paper",
                None,
                "2026-09-23T20:00:00+00:00",
                "2026-09-23",
                "day",
                "2026-09-23T20:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                SNAPSHOT,
                REVISION,
                WORK,
                evidence.object_id,
                extracted.object_id,
                "parser-v1",
                "abstract_only",
                "{}",
                "e" * 64,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO model_runs("
            "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,state,started_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (
                RUN,
                "abstract_reading_card",
                "openrouter",
                "model",
                "f" * 64,
                GENERATION,
                "succeeded",
                "2026-09-23T20:10:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO summary_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                SUMMARY,
                REVISION,
                WORK,
                SNAPSHOT,
                GENERATION,
                RUN,
                summary.object_id,
                "zh-TW",
                "plain-zh-TW-v1",
                "passed",
                "2026-09-23T20:15:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO current_summaries VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "zh-TW",
                "plain-zh-TW-v1",
                SUMMARY,
                REVISION,
                GENERATION,
                1,
            ),
        )
        connection.execute(
            "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
            (
                "event:runtime",
                WORK,
                REVISION,
                "new_work",
                "event-key:runtime",
                "{}",
                None,
                "2026-09-23T23:30:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO relevance_assessments VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "relevance:runtime",
                "personal",
                1,
                REVISION,
                "1" * 64,
                "direct",
                "succeeded",
                '{"anchor_ids":["anchor:runtime"],"recommendation_reason":"直接研究羽球"}',
                "2026-09-23T23:40:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO relevance_assessment_domains VALUES(?,?,?,?)",
            ("relevance:runtime", "badminton", 1, SNAPSHOT),
        )
        connection.execute(
            "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "subscription:daily",
                "reader:local",
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
    finally:
        connection.close()

    return SqliteSchemaConnectionFactory(
        root,
        load_workspace_migrations(with_runtime=True),
        minimum_version=9,
    )


def test_worker_builds_latest_digest_outbox_without_smtp_side_effect(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    schema = _seed_workspace(root)
    raw = SqliteConnectionFactory(root)
    files = FilesystemObjectBytesAdapter(root)
    objects = SqliteObjectUnitOfWorkAdapter(raw)
    read_object = ReadObject(files, objects)
    publish_object = PublishObject(files, objects)

    jobs = SqliteWorkflowJobStoreAdapter(schema.connect)
    delivery_store = SqliteDeliveryStoreAdapter(schema.connect)
    digest_artifacts = KernelDigestArtifactAdapter(publish_object, read_object)
    scheduled_digest = PrepareScheduledDigest(
        events=SqliteDigestResearchEventAdapter(schema.connect),
        summaries=SqliteDigestCurrentSummaryAdapter(schema.connect, read_object),
        relevance=SqliteDigestRelevanceAdapter(schema.connect),
        context=SqliteDigestDeliveryContextAdapter(schema.connect),
        queue=QueueDigest(digest_artifacts, delivery_store),
    )
    clock = FixedClock()
    cycle = RunWorkerCycle(
        RunSchedulerTick(SqliteSchedulerInputAdapter(schema.connect), jobs),
        ProcessWorkflowJob(
            store=jobs,
            builder=Mock(),
            harvest=None,
            clock=clock,
            live_source_enabled=False,
            digest=scheduled_digest,
        ),
        clock,
    )

    result = cycle(
        "worker:integration",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )

    assert result.scheduler.new_jobs == 1
    assert result.scheduler.digest_deferred is False
    assert [(gap.kind, gap.reason) for gap in result.scheduler.coverage_gaps] == [
        ("harvest", "source_scheduler_not_supported")
    ]
    assert result.processed_jobs == 1
    assert result.jobs[0].job_kind == "prepare_digest"
    assert result.jobs[0].state == "succeeded"

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT state FROM workflow_jobs WHERE job_kind='prepare_digest'"
        ).fetchone()[0] == "succeeded"
        digest = connection.execute(
            "SELECT period_key,state,rendered_object_id FROM digests"
        ).fetchone()
        assert digest["period_key"] == "2026-09-24"
        assert digest["state"] == "queued"
        assert digest["rendered_object_id"].startswith("digest:")
        assert connection.execute("SELECT count(*) FROM digest_items").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM delivery_outbox").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM notification_ledger").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM delivery_attempts").fetchone()[0] == 0
        assert connection.execute(
            "SELECT external_effects_enabled FROM workspace_metadata WHERE singleton=1"
        ).fetchone()[0] == 0
    finally:
        connection.close()

    digest_bytes = read_object(digest["rendered_object_id"])
    payload = json.loads(digest_bytes.decode("utf-8"))
    assert payload["period_key"] == "2026-09-24"
    assert payload["items"][0]["event_id"] == "event:runtime"
    assert "資料覆蓋提醒" in payload["text_body"]
    assert "source_scheduler_not_supported" in payload["text_body"]
