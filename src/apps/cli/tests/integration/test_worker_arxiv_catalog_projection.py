import hashlib
import inspect
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from injector import Injector

from apps.cli.adapters.driving.run_worker import run_worker_cli
from apps.cli.model_commissioning import (
    commissioned_openrouter_budget_period,
    commissioned_openrouter_execution_policy_fingerprint,
    commissioned_openrouter_reservation_micros,
)
from apps.cli.mvp_profile_config import bundled_domain_seeds_json
from apps.cli.module import WorkerCliModule
from libs.delivery.dtos.delivery_dispatch import MailSendResult
from libs.delivery.dtos.scheduled_digest import ScheduledDigestRequest
from libs.delivery.ports.prepare_scheduled_digest_port import (
    PrepareScheduledDigestPort,
)
from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import (
    ArxivAtomParserAdapter,
)
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import (
    ArxivQueryCompilerAdapter,
)
from libs.discovery.adapters.driven.arxiv_source_adapter import ArxivSourceAdapter
from libs.discovery.adapters.driven.http_client_arxiv_transport_adapter import (
    HttpClientArxivTransportAdapter,
)
from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import (
    PosixArxivRateLimitAdapter,
)
from libs.discovery.application.queries.fetch_source_page import FetchSourcePage
from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import (
    SqliteConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.paper_explanations.dtos.generation_budget_policy import (
    GenerationBudgetPolicy,
)
from libs.paper_explanations.dtos.model_http_response import ModelHttpResponse
from libs.paper_explanations.dtos.structured_generation_result import (
    GenerationReceipt,
    StructuredGenerationResult,
)
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.research_workflow.dtos.revision_notice import RevisionNoticeRequest
from libs.research_workflow.dtos.workflow_job import EnqueueWorkflowJob
from libs.research_workflow.ports.process_revision_notice_port import (
    ProcessRevisionNoticePort,
)
from libs.research_workflow.ports.process_workflow_job_port import (
    ProcessWorkflowJobPort,
)
from libs.research_workflow.ports.run_worker_cycle_port import RunWorkerCyclePort
from libs.research_workflow.ports.workflow_job_store_port import WorkflowJobStorePort
from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import (
    SqliteWatchProfileStoreAdapter,
)
from libs.watch_profiles.domain.services.normalize_watch_configuration import (
    NormalizeWatchConfiguration,
)


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


class FakeArxivTransport:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, request):
        self.calls += 1
        body = (
            "<feed xmlns='http://www.w3.org/2005/Atom' "
            "xmlns:s='http://a9.com/-/spec/opensearch/1.1/'>"
            "<s:totalResults>1</s:totalResults>"
            "<s:startIndex>0</s:startIndex>"
            "<s:itemsPerPage>1</s:itemsPerPage>"
            "<entry>"
            "<id>https://arxiv.org/abs/2609.00001v1</id>"
            "<title>Synthetic arXiv study</title>"
            "<summary>Abstract evidence from arXiv.</summary>"
            "<author><name>Fixture Author</name></author>"
            "<category term='stat.ML'/>"
            "<published>2026-09-22T00:00:00Z</published>"
            "<updated>2026-09-22T00:00:00Z</updated>"
            "</entry></feed>"
        ).encode()
        return SourceHttpResponse(
            200,
            body,
            (("content-type", "application/atom+xml"),),
            NOW,
            None,
        )


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 24

    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
            (
                "statistics",
                "統計學",
                '{"aliases":["statistics"],"exclude":[],"include":["statistics"],'
                '"source_categories":{"arxiv":["stat.ML"]},"sources":["arxiv"]}',
                1,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
            ("personal", "reader:local", "mine", "active", None, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
            (
                "personal",
                1,
                "統計學",
                '{"allow_preprints":true,"exclude":[],"free_only":false,'
                '"include":[],"languages":["english"],"sources":["arxiv"]}',
                "a" * 64,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
            ("personal", 1, "statistics", 1),
        )
        connection.execute(
            "UPDATE watch_profiles SET published_revision=1 WHERE id='personal'"
        )
        connection.commit()
    finally:
        connection.close()
    return root


def test_worker_arxiv_uses_workspace_rate_limit_state_by_default(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    transport = FakeArxivTransport()
    worker = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                transport=transport,
            )
        ],
        auto_bind=False,
    ).get(RunWorkerCyclePort)

    result = worker(
        "worker:arxiv-default-rate-state",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )

    assert [(job.job_kind, job.state) for job in result.jobs] == [
        ("harvest_window", "succeeded"),
    ]
    assert transport.calls == 1
    assert (root / "state/arxiv-rate-limit.json").is_file()

def test_worker_arxiv_harvest_projects_versioned_preprint_and_evidence(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    transport = FakeArxivTransport()
    rate_state = tmp_path / "arxiv-rate.json"
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(rate_state),
                transport=transport,
            )
        ],
        auto_bind=False,
    )

    result = injector.get(RunWorkerCyclePort)(
        "worker:arxiv",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )

    assert result.scheduler.new_jobs == 1
    assert result.scheduler.coverage_gaps == ()
    assert result.processed_jobs == 1
    assert result.jobs[0].state == "succeeded"
    assert transport.calls == 1

    connection = SqliteConnectionFactory(root).connect()
    try:
        unit = connection.execute(
            "SELECT id,state,checkpoint_version FROM harvest_units"
        ).fetchone()
        assert unit["state"] == "succeeded"

        manifestation = connection.execute(
            "SELECT source_namespace,native_id,manifestation_kind "
            "FROM paper_manifestations"
        ).fetchone()
        assert tuple(manifestation) == (
            "arxiv",
            "2609.00001",
            "preprint",
        )

        revision = connection.execute(
            "SELECT id,native_version,title FROM paper_revisions"
        ).fetchone()
        assert revision["native_version"] == "1"
        assert revision["title"] == "Synthetic arXiv study"

        event = connection.execute(
            "SELECT revision_id,event_kind FROM research_events"
        ).fetchone()
        assert tuple(event) == (revision["id"], "revision_available")

        snapshot = connection.execute(
            "SELECT revision_id,evidence_level,parser_version "
            "FROM evidence_snapshots"
        ).fetchone()
        assert snapshot["revision_id"] == revision["id"]
        assert snapshot["evidence_level"] == "abstract_only"
        assert snapshot["parser_version"] == "arxiv-atom-v1:abstract-v1"

        progress = connection.execute(
            "SELECT unit_id,source,projected_count,state "
            "FROM source_catalog_projection_progress"
        ).fetchone()
        assert tuple(progress) == (
            unit["id"],
            "arxiv",
            1,
            "succeeded",
        )
    finally:
        connection.close()


class FakeStructuredGenerator:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.provider_generation_ids: list[str] = []

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def __call__(self, request):
        self.calls.append(request.task_kind)
        payload = json.loads(request.payload_json)
        if request.task_kind == "claim_extraction":
            content = {
                "schema_version": "paper-claims-v1",
                "snapshot_id": payload["snapshot_id"],
                "input_fingerprint": request.input_fingerprint,
                "claims": [
                    {
                        "claim_type": "result",
                        "anchor_ids": [payload["anchors"][0]["anchor_id"]],
                    }
                ],
                "not_reported_in_read_evidence": [],
            }
        elif request.task_kind == "relevance_assessment":
            content = {
                "schema_version": "paper-relevance-v1",
                "snapshot_id": payload["evidence"]["snapshot_id"],
                "input_fingerprint": request.input_fingerprint,
                "profile_id": payload["profile"]["profile_id"],
                "domain_id": payload["domain"]["domain_id"],
                "profile_revision": payload["profile"]["revision"],
                "domain_revision": payload["domain"]["revision"],
                "decision": "direct",
                "recommendation_reason": "The supplied abstract directly concerns the selected domain.",
                "anchor_ids": [payload["evidence"]["anchors"][0]["anchor_id"]],
            }
        elif request.task_kind == "abstract_reading_card":
            content = {
                "schema_version": "reading-card-v1",
                "snapshot_id": payload["snapshot_id"],
                "input_fingerprint": request.input_fingerprint,
                "language": "zh-TW",
                "faithful_translation": [
                    {
                        "text": "繁中翻譯測試：" + payload["source_text"],
                        "anchor_ids": [payload["anchors"][0]["anchor_id"]],
                    }
                ],
                "plain_language_card": [
                    {
                        "claim_type": payload["claims"][0]["claim_type"],
                        "text": "這份研究提供 arXiv 摘要證據。",
                        "claim_ids": [payload["claims"][0]["claim_id"]],
                    }
                ],
            }
        elif request.task_kind == "support_verification":
            content = {
                "schema_version": "support-verification-v1",
                "snapshot_id": payload["snapshot_id"],
                "input_fingerprint": request.input_fingerprint,
                "statements": [
                    {
                        "statement_index": item["statement_index"],
                        "verdict": "supported",
                        "claim_ids": item["claim_ids"],
                    }
                    for item in payload["statements"]
                ],
            }
        else:
            raise AssertionError("unexpected structured generation task: " + request.task_kind)

        generation_id = f"provider-fixture-{len(self.calls)}"
        self.provider_generation_ids.append(generation_id)
        request_sha = hashlib.sha256(
            (request.task_kind + "\0" + request.input_fingerprint).encode()
        ).hexdigest()
        return StructuredGenerationResult(
            self._json(content),
            GenerationReceipt(
                request.input_fingerprint,
                request_sha,
                generation_id,
                request.model_name,
                request.model_name,
                "Fixture Provider",
                10,
                5,
                "0.000001",
                "stop",
            ),
        )


def test_worker_arxiv_projection_runs_tracked_explanation_to_current_summary(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    transport = FakeArxivTransport()
    generator = FakeStructuredGenerator()
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(tmp_path / "arxiv-rate-explanation.json"),
                transport=transport,
                structured_generation=generator,
                generation_budget_policy=GenerationBudgetPolicy(
                    "2026-09",
                    "USD",
                    1_000_000,
                    10_000,
                    "b" * 64,
                ),
            )
        ],
        auto_bind=False,
    )

    result = injector.get(RunWorkerCyclePort)(
        "worker:arxiv-explanation",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )

    assert result.scheduler.new_jobs == 1
    assert result.scheduler.coverage_gaps == ()
    assert result.processed_jobs == 2
    assert [(job.job_kind, job.state) for job in result.jobs] == [
        ("harvest_window", "succeeded"),
        ("explain_snapshot", "succeeded"),
    ]
    assert transport.calls == 1
    assert generator.calls == [
        "claim_extraction",
        "relevance_assessment",
        "abstract_reading_card",
        "support_verification",
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        relevance = connection.execute(
            "SELECT execution_state,decision FROM relevance_assessments"
        ).fetchone()
        assert tuple(relevance) == ("succeeded", "direct")

        summary = connection.execute(
            "SELECT id,revision_id,work_id,snapshot_id,generation_run_id,qa_state "
            "FROM summary_revisions"
        ).fetchone()
        assert summary["qa_state"] == "passed"
        assert summary["generation_run_id"].startswith("run:")

        current = connection.execute(
            "SELECT summary_id,revision_id,pointer_version FROM current_summaries"
        ).fetchone()
        assert tuple(current) == (
            summary["id"],
            summary["revision_id"],
            1,
        )

        runs = connection.execute(
            "SELECT id,task_kind,state FROM model_runs ORDER BY task_kind"
        ).fetchall()
        assert len(runs) == 4
        assert {row["task_kind"] for row in runs} == set(generator.calls)
        assert all(row["state"] == "succeeded" for row in runs)
        assert all(row["id"].startswith("run:") for row in runs)
        assert not {
            row["id"] for row in runs
        } & set(generator.provider_generation_ids)

        jobs = connection.execute(
            "SELECT job_kind,state FROM workflow_jobs ORDER BY created_at,id"
        ).fetchall()
        assert [(row["job_kind"], row["state"]) for row in jobs] == [
            ("harvest_window", "succeeded"),
            ("explain_snapshot", "succeeded"),
        ]
    finally:
        connection.close()


def test_cross_domain_explanation_reuses_domain_independent_generation(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
            (
                "machine_learning",
                "機器學習",
                '{"aliases":["machine learning"],"exclude":[],'
                '"include":["machine learning"],'
                '"source_categories":{"arxiv":["stat.ML"]},'
                '"sources":["arxiv"]}',
                1,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
            ("personal", 1, "machine_learning", 1),
        )
        connection.commit()
    finally:
        connection.close()

    transport = FakeArxivTransport()
    generator = FakeStructuredGenerator()
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(
                    tmp_path / "arxiv-rate-cross-domain-cache.json"
                ),
                transport=transport,
                structured_generation=generator,
                generation_budget_policy=GenerationBudgetPolicy(
                    "2026-09",
                    "USD",
                    1_000_000,
                    10_000,
                    "f" * 64,
                ),
            )
        ],
        auto_bind=False,
    )
    worker = injector.get(RunWorkerCyclePort)

    first = worker(
        "worker:cross-domain-first",
        max_new_jobs=1,
        max_jobs=10,
        lease_seconds=300,
    )

    assert [(job.job_kind, job.state) for job in first.jobs] == [
        ("harvest_window", "succeeded"),
        ("explain_snapshot", "succeeded"),
    ]
    assert generator.calls == [
        "claim_extraction",
        "relevance_assessment",
        "abstract_reading_card",
        "support_verification",
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        original = connection.execute(
            "SELECT input_json FROM workflow_jobs "
            "WHERE job_kind='explain_snapshot'"
        ).fetchone()
        payload = json.loads(original["input_json"])
        assert payload["domain_id"] == "machine_learning"
    finally:
        connection.close()

    payload["domain_id"] = "statistics"
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    injector.get(WorkflowJobStorePort).enqueue(
        EnqueueWorkflowJob(
            job_kind="explain_snapshot",
            business_key="explain:cross-domain-cache:statistics",
            input_json=encoded,
            input_fingerprint=hashlib.sha256(encoded.encode()).hexdigest(),
            due_at=NOW,
            created_at=NOW,
        )
    )

    second = injector.get(ProcessWorkflowJobPort)(
        "worker:cross-domain-second",
        lease_seconds=300,
    )

    assert (second.job_kind, second.state) == (
        "explain_snapshot",
        "succeeded",
    )
    assert generator.calls == [
        "claim_extraction",
        "relevance_assessment",
        "abstract_reading_card",
        "support_verification",
        "relevance_assessment",
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        counts = {
            row["task_kind"]: row["n"]
            for row in connection.execute(
                "SELECT task_kind,COUNT(*) AS n FROM model_runs "
                "GROUP BY task_kind"
            )
        }
        domains = {
            row["domain_id"]
            for row in connection.execute(
                "SELECT domain_id FROM relevance_assessment_domains"
            )
        }
        assert counts == {
            "abstract_reading_card": 1,
            "claim_extraction": 1,
            "relevance_assessment": 2,
            "support_verification": 1,
        }
        assert domains == {"machine_learning", "statistics"}
        assert connection.execute(
            "SELECT COUNT(*) FROM summary_revisions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM current_summaries"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_worker_arxiv_explanation_replay_reuses_generation_and_owner_state(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    transport = FakeArxivTransport()
    generator = FakeStructuredGenerator()
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(tmp_path / "arxiv-rate-explanation-replay.json"),
                transport=transport,
                structured_generation=generator,
                generation_budget_policy=GenerationBudgetPolicy(
                    "2026-09",
                    "USD",
                    1_000_000,
                    10_000,
                    "c" * 64,
                ),
            )
        ],
        auto_bind=False,
    )
    worker = injector.get(RunWorkerCyclePort)

    first = worker(
        "worker:arxiv-explanation-replay",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )
    assert any(job.job_kind == "explain_snapshot" for job in first.jobs)
    assert len(generator.calls) == 4

    connection = SqliteConnectionFactory(root).connect()
    try:
        before = tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "model_runs",
                "relevance_assessments",
                "summary_revisions",
                "current_summaries",
            )
        )
        pointer_before = connection.execute(
            "SELECT summary_id,pointer_version FROM current_summaries"
        ).fetchone()
        assert before == (4, 1, 1, 1)
        assert pointer_before["pointer_version"] == 1
    finally:
        connection.close()

    worker(
        "worker:arxiv-explanation-replay",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )

    assert len(generator.calls) == 4

    connection = SqliteConnectionFactory(root).connect()
    try:
        after = tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "model_runs",
                "relevance_assessments",
                "summary_revisions",
                "current_summaries",
            )
        )
        pointer_after = connection.execute(
            "SELECT summary_id,pointer_version FROM current_summaries"
        ).fetchone()
        assert after == before
        assert tuple(pointer_after) == tuple(pointer_before)
    finally:
        connection.close()


def test_two_workers_fence_one_durable_explanation_job(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    transport = FakeArxivTransport()
    harvest_only = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(tmp_path / "arxiv-rate-fencing.json"),
                transport=transport,
            )
        ],
        auto_bind=False,
    ).get(RunWorkerCyclePort)

    harvested = harvest_only(
        "worker:harvest-only",
        max_new_jobs=10,
        max_jobs=1,
        lease_seconds=300,
    )

    assert harvested.processed_jobs == 1
    assert harvested.jobs[0].job_kind == "harvest_window"
    assert harvested.jobs[0].state == "succeeded"

    connection = SqliteConnectionFactory(root).connect()
    try:
        pending = connection.execute(
            "SELECT state FROM workflow_jobs WHERE job_kind='explain_snapshot'"
        ).fetchone()
        assert pending["state"] == "pending"
    finally:
        connection.close()

    generator = FakeStructuredGenerator()
    budget = GenerationBudgetPolicy(
        "2026-09",
        "USD",
        1_000_000,
        10_000,
        "d" * 64,
    )
    processor_a = Injector(
        [
            WorkerCliModule(
                str(root),
                structured_generation=generator,
                generation_budget_policy=budget,
            )
        ],
        auto_bind=False,
    ).get(ProcessWorkflowJobPort)
    processor_b = Injector(
        [
            WorkerCliModule(
                str(root),
                structured_generation=generator,
                generation_budget_policy=budget,
            )
        ],
        auto_bind=False,
    ).get(ProcessWorkflowJobPort)

    barrier = Barrier(2)

    def process(processor, owner):
        barrier.wait(timeout=5)
        return processor(owner, lease_seconds=300)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(process, processor_a, "worker:explain-a")
        future_b = pool.submit(process, processor_b, "worker:explain-b")
        outcomes = (future_a.result(timeout=10), future_b.result(timeout=10))

    assert sorted(result.state for result in outcomes) == ["idle", "succeeded"]
    succeeded = next(result for result in outcomes if result.state == "succeeded")
    assert succeeded.job_kind == "explain_snapshot"
    assert generator.calls == [
        "claim_extraction",
        "relevance_assessment",
        "abstract_reading_card",
        "support_verification",
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM model_runs"
        ).fetchone()[0] == 4
        assert connection.execute(
            "SELECT count(*) FROM relevance_assessments"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM summary_revisions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM current_summaries"
        ).fetchone()[0] == 1
        attempts = connection.execute(
            "SELECT count(*) FROM job_attempts a "
            "JOIN workflow_jobs j ON j.id=a.job_id "
            "WHERE j.job_kind='explain_snapshot'"
        ).fetchone()[0]
        assert attempts == 1
    finally:
        connection.close()


def test_daily_digest_waits_for_explanation_then_queues_generated_summary(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO delivery_subscriptions("
            "id,reader_id,channel,enabled,timezone,schedule_json,max_items,"
            "recipient_ref,policy_version,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
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

    transport = FakeArxivTransport()
    generator = FakeStructuredGenerator()
    worker = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(tmp_path / "arxiv-rate-digest-ordering.json"),
                transport=transport,
                structured_generation=generator,
                generation_budget_policy=GenerationBudgetPolicy(
                    "2026-09",
                    "USD",
                    1_000_000,
                    10_000,
                    "e" * 64,
                ),
            )
        ],
        auto_bind=False,
    ).get(RunWorkerCyclePort)

    harvested = worker(
        "worker:arxiv-digest-ordering",
        max_new_jobs=10,
        max_jobs=1,
        lease_seconds=300,
    )
    assert harvested.scheduler.digest_deferred is True
    assert [(job.job_kind, job.state) for job in harvested.jobs] == [
        ("harvest_window", "succeeded"),
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        assert connection.execute(
            "SELECT state FROM workflow_jobs WHERE job_kind='explain_snapshot'"
        ).fetchone()[0] == "pending"
        assert connection.execute(
            "SELECT count(*) FROM workflow_jobs WHERE job_kind='prepare_digest'"
        ).fetchone()[0] == 0
    finally:
        connection.close()

    explained = worker(
        "worker:arxiv-digest-ordering",
        max_new_jobs=10,
        max_jobs=1,
        lease_seconds=300,
    )
    assert explained.scheduler.new_jobs == 0
    assert explained.scheduler.digest_deferred is True
    assert [(job.job_kind, job.state) for job in explained.jobs] == [
        ("explain_snapshot", "succeeded"),
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM current_summaries"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM relevance_assessments "
            "WHERE execution_state='succeeded' AND decision='direct'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM workflow_jobs WHERE job_kind='prepare_digest'"
        ).fetchone()[0] == 0
    finally:
        connection.close()

    digested = worker(
        "worker:arxiv-digest-ordering",
        max_new_jobs=10,
        max_jobs=1,
        lease_seconds=300,
    )
    assert digested.scheduler.new_jobs == 1
    assert digested.scheduler.digest_deferred is False
    assert [(job.job_kind, job.state) for job in digested.jobs] == [
        ("prepare_digest", "succeeded"),
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        digest = connection.execute(
            "SELECT state,period_key FROM digests"
        ).fetchone()
        assert tuple(digest) == ("queued", "2026-09-24")
        assert connection.execute(
            "SELECT count(*) FROM digest_items"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM delivery_outbox"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM notification_ledger"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM delivery_attempts"
        ).fetchone()[0] == 0
    finally:
        connection.close()

    assert transport.calls == 1
    assert generator.calls == [
        "claim_extraction",
        "relevance_assessment",
        "abstract_reading_card",
        "support_verification",
    ]


class UnavailableStructuredGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request):
        self.calls += 1
        raise ModelGatewayError("endpoint_unavailable")


def test_awaiting_explanation_allows_coverage_only_empty_digest_completion(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO delivery_subscriptions("
            "id,reader_id,channel,enabled,timezone,schedule_json,max_items,"
            "recipient_ref,policy_version,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
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

    transport = FakeArxivTransport()
    generator = UnavailableStructuredGenerator()
    worker = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(tmp_path / "arxiv-rate-digest-awaiting.json"),
                transport=transport,
                structured_generation=generator,
                generation_budget_policy=GenerationBudgetPolicy(
                    "2026-09",
                    "USD",
                    1_000_000,
                    10_000,
                    "f" * 64,
                ),
            )
        ],
        auto_bind=False,
    ).get(RunWorkerCyclePort)

    harvested = worker(
        "worker:arxiv-digest-awaiting",
        max_new_jobs=10,
        max_jobs=1,
        lease_seconds=300,
    )
    assert harvested.scheduler.digest_deferred is True
    assert [(job.job_kind, job.state) for job in harvested.jobs] == [
        ("harvest_window", "succeeded"),
    ]

    explanation = worker(
        "worker:arxiv-digest-awaiting",
        max_new_jobs=10,
        max_jobs=1,
        lease_seconds=300,
    )
    assert explanation.scheduler.digest_deferred is True
    assert [(job.job_kind, job.state) for job in explanation.jobs] == [
        ("explain_snapshot", "awaiting_external"),
    ]
    assert generator.calls == 1

    connection = SqliteConnectionFactory(root).connect()
    try:
        explanation_job = connection.execute(
            "SELECT business_key,state FROM workflow_jobs "
            "WHERE job_kind='explain_snapshot'"
        ).fetchone()
        assert explanation_job["state"] == "awaiting_external"
        explanation_key = explanation_job["business_key"]
        assert connection.execute(
            "SELECT count(*) FROM current_summaries"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM relevance_assessments"
        ).fetchone()[0] == 0
        run = connection.execute(
            "SELECT task_kind,state,error_code FROM model_runs"
        ).fetchone()
        assert tuple(run) == (
            "claim_extraction",
            "failed",
            "endpoint_unavailable",
        )
    finally:
        connection.close()

    digested = worker(
        "worker:arxiv-digest-awaiting",
        max_new_jobs=10,
        max_jobs=1,
        lease_seconds=300,
    )
    assert digested.scheduler.digest_deferred is False
    assert [
        (gap.kind, gap.identity, gap.reason)
        for gap in digested.scheduler.coverage_gaps
    ] == [
        (
            "explanation",
            explanation_key,
            "explanation_awaiting_external",
        )
    ]
    assert [(job.job_kind, job.state) for job in digested.jobs] == [
        ("prepare_digest", "succeeded"),
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        digest_job = connection.execute(
            "SELECT input_json FROM workflow_jobs "
            "WHERE job_kind='prepare_digest'"
        ).fetchone()
        digest_input = json.loads(digest_job["input_json"])
        assert digest_input["coverage_gaps"] == [
            {
                "identity": explanation_key,
                "kind": "explanation",
                "reason": "explanation_awaiting_external",
            }
        ]
        for table in (
            "digests",
            "digest_items",
            "delivery_outbox",
            "notification_ledger",
            "delivery_attempts",
        ):
            assert connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0] == 0
    finally:
        connection.close()

    assert transport.calls == 1
    assert generator.calls == 1


class MvpArxivTransport:
    def __init__(self, observed_at: datetime) -> None:
        self.observed_at = observed_at
        self.calls = 0

    def get(self, request):
        del request
        self.calls += 1
        body = (
            "<feed xmlns='http://www.w3.org/2005/Atom' "
            "xmlns:s='http://a9.com/-/spec/opensearch/1.1/'>"
            "<s:totalResults>1</s:totalResults>"
            "<s:startIndex>0</s:startIndex>"
            "<s:itemsPerPage>1</s:itemsPerPage>"
            "<entry>"
            "<id>https://arxiv.org/abs/2609.00001v1</id>"
            "<title>Synthetic arXiv study</title>"
            "<summary>Abstract evidence from arXiv.</summary>"
            "<author><name>Fixture Author</name></author>"
            "<category term='stat.ML'/>"
            "<published>2026-09-22T00:00:00Z</published>"
            "<updated>2026-09-22T00:00:00Z</updated>"
            "</entry></feed>"
        ).encode()
        return SourceHttpResponse(
            200,
            body,
            (("content-type", "application/atom+xml"),),
            self.observed_at,
            None,
        )


class FakeOpenRouterHttpTransport:
    def __init__(self) -> None:
        self.schemas: list[str] = []

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def post(self, body: bytes) -> ModelHttpResponse:
        request = json.loads(body.decode("utf-8"))
        schema_name = request["response_format"]["json_schema"]["name"]
        user = json.loads(request["messages"][1]["content"])
        payload = user["data"]
        input_fingerprint = user["input_fingerprint"]
        self.schemas.append(schema_name)

        if schema_name == "paper_claims":
            content = {
                "schema_version": "paper-claims-v1",
                "snapshot_id": payload["snapshot_id"],
                "input_fingerprint": input_fingerprint,
                "claims": [
                    {
                        "claim_type": "result",
                        "anchor_ids": [payload["anchors"][0]["anchor_id"]],
                    }
                ],
                "not_reported_in_read_evidence": [],
            }
        elif schema_name == "paper_relevance":
            content = {
                "schema_version": "paper-relevance-v1",
                "snapshot_id": payload["evidence"]["snapshot_id"],
                "input_fingerprint": input_fingerprint,
                "profile_id": payload["profile"]["profile_id"],
                "domain_id": payload["domain"]["domain_id"],
                "profile_revision": payload["profile"]["revision"],
                "domain_revision": payload["domain"]["revision"],
                "decision": "direct",
                "recommendation_reason": "摘要內容直接符合統計學關注範圍。",
                "anchor_ids": [payload["evidence"]["anchors"][0]["anchor_id"]],
            }
        elif schema_name == "reading_card":
            content = {
                "schema_version": "reading-card-v1",
                "snapshot_id": payload["snapshot_id"],
                "input_fingerprint": input_fingerprint,
                "language": "zh-TW",
                "faithful_translation": [
                    {
                        "text": "繁中翻譯測試：" + payload["source_text"],
                        "anchor_ids": [payload["anchors"][0]["anchor_id"]],
                    }
                ],
                "plain_language_card": [
                    {
                        "claim_type": payload["claims"][0]["claim_type"],
                        "text": "這份研究提供 arXiv 摘要證據。",
                        "claim_ids": [payload["claims"][0]["claim_id"]],
                    }
                ],
            }
        elif schema_name == "support_verification":
            content = {
                "schema_version": "support-verification-v1",
                "snapshot_id": payload["snapshot_id"],
                "input_fingerprint": input_fingerprint,
                "statements": [
                    {
                        "statement_index": item["statement_index"],
                        "verdict": "supported",
                        "claim_ids": item["claim_ids"],
                    }
                    for item in payload["statements"]
                ],
            }
        else:
            raise AssertionError("unexpected OpenRouter schema: " + schema_name)

        envelope = {
            "id": f"gen-mvp-{len(self.schemas)}",
            "model": request["model"],
            "provider": "Fixture Provider",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": self._canonical(content),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost": 0.000001,
            },
        }
        return ModelHttpResponse(
            200,
            self._canonical(envelope).encode("utf-8"),
        )


class CapturingMailSender:
    def __init__(self) -> None:
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return MailSendResult("provider_accepted", "provider:mvp-fixture", None)


class MvpRecipientResolver:
    def resolve(self, recipient_ref):
        assert recipient_ref == "recipient:primary"
        return "reader@example.com"


def test_mvp_one_arxiv_paper_becomes_one_traditional_chinese_email(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    setup_now = datetime.now(timezone.utc)
    observed_at = setup_now - timedelta(minutes=2)
    schedule_time = setup_now.strftime("%H:%M")

    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO delivery_subscriptions("
            "id,reader_id,channel,enabled,timezone,schedule_json,max_items,"
            "recipient_ref,policy_version,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "subscription:mvp-daily",
                "reader:local",
                "email",
                1,
                "UTC",
                json.dumps(
                    {"kind": "daily", "local_time": schedule_time},
                    separators=(",", ":"),
                ),
                5,
                "recipient:primary",
                1,
                setup_now.isoformat(),
            ),
        )
        connection.execute(
            "UPDATE workspace_metadata SET external_effects_enabled=1 "
            "WHERE singleton=1"
        )
        connection.commit()
    finally:
        connection.close()

    source = MvpArxivTransport(observed_at)
    model = FakeOpenRouterHttpTransport()
    mail = CapturingMailSender()
    worker = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(tmp_path / "arxiv-rate-mvp.json"),
                transport=source,
                allow_live_model=True,
                model_api_key="sk-or-v1-fake-mvp-secret-000000",
                model_http_transport=model,
                generation_budget_policy=GenerationBudgetPolicy(
                    setup_now.strftime("%Y-%m"),
                    "USD",
                    1_000_000,
                    10_000,
                    "1" * 64,
                ),
                allow_live_mail=True,
                mail_sender=mail,
                recipient_resolver=MvpRecipientResolver(),
            )
        ],
        auto_bind=False,
    ).get(RunWorkerCyclePort)

    generated = worker(
        "worker:mvp-paper-email",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )
    assert [(job.job_kind, job.state) for job in generated.jobs] == [
        ("harvest_window", "succeeded"),
        ("explain_snapshot", "succeeded"),
    ]

    digested = worker(
        "worker:mvp-paper-email",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )
    assert [(job.job_kind, job.state) for job in digested.jobs] == [
        ("prepare_digest", "succeeded"),
    ]

    delivered = worker(
        "worker:mvp-paper-email",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )
    assert [(job.job_kind, job.state) for job in delivered.jobs] == [
        ("dispatch_digest", "succeeded"),
    ]

    assert source.calls == 1
    assert model.schemas == [
        "paper_claims",
        "paper_relevance",
        "reading_card",
        "support_verification",
    ]
    assert len(mail.messages) == 1
    message = mail.messages[0]
    assert message.recipient == "reader@example.com"
    assert "每日精選 1 篇" in message.subject
    assert "Synthetic arXiv study" in message.text_body
    assert "這份研究提供 arXiv 摘要證據。" in message.text_body

    replay = worker(
        "worker:mvp-paper-email",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )
    assert replay.processed_jobs == 0
    assert len(mail.messages) == 1

    connection = SqliteConnectionFactory(root).connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM current_summaries"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM digests"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM delivery_outbox"
        ).fetchone()[0] == "provider_accepted"
        assert connection.execute(
            "SELECT count(*) FROM delivery_attempts"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_worker_env_active_pipeline_reaches_one_email(
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "env-active-runtime"
    info = SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    assert info.schema_version == 24

    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE workspace_metadata SET external_effects_enabled=1 "
            "WHERE singleton=1"
        )
        connection.commit()
    finally:
        connection.close()

    setup_clock = datetime.now(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    digest_clock = setup_clock + timedelta(minutes=30)
    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "\n".join(
            (
                f"PAPER_RADAR_WORKSPACE={root}",
                "PAPER_RADAR_ALLOW_LIVE_SOURCE=true",
                "PAPER_RADAR_ALLOW_LIVE_MODEL=true",
                "PAPER_RADAR_ALLOW_LIVE_MAIL=true",
                "PAPER_RADAR_PROFILE_DOMAINS=statistics",
                "PAPER_RADAR_PROFILE_SCOPE=關注統計與機器學習的新 arXiv 論文。",
                "PAPER_RADAR_OPENROUTER_API_KEY="
                "sk-or-v1-fake-env-pipeline-000000",
                "PAPER_RADAR_MODEL_MONTHLY_BUDGET_USD=2.00",
                "PAPER_RADAR_RECIPIENT_EMAIL=reader@example.com",
                "PAPER_RADAR_SMTP_HOST=smtp.example.com",
                "PAPER_RADAR_SMTP_PORT=465",
                "PAPER_RADAR_SMTP_SENDER=paper-radar@example.com",
                "PAPER_RADAR_SMTP_USERNAME=mailer@example.com",
                "PAPER_RADAR_SMTP_PASSWORD=fake-smtp-password",
                "PAPER_RADAR_SMTP_SECURITY=ssl",
                "PAPER_RADAR_DIGEST_TIMEZONE=UTC",
                "PAPER_RADAR_DIGEST_LOCAL_TIME="
                + digest_clock.strftime("%H:%M"),
                "PAPER_RADAR_DIGEST_MAX_ITEMS=5",
                "PAPER_RADAR_MAX_NEW_JOBS=10",
                "PAPER_RADAR_MAX_JOBS=10",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    class RuntimeTimestampMvpArxivTransport(MvpArxivTransport):
        def get(self, request):
            self.observed_at = datetime.now(timezone.utc)
            return super().get(request)

    workflow_clock = None
    source = RuntimeTimestampMvpArxivTransport(setup_clock)
    model = FakeOpenRouterHttpTransport()
    mail = CapturingMailSender()

    def worker_module_factory(workspace: str, **kwargs):
        return WorkerCliModule(
            workspace,
            **kwargs,
            transport=source,
            model_http_transport=model,
            mail_sender=mail,
            recipient_resolver=MvpRecipientResolver(),
            workflow_clock=workflow_clock,
        )

    outputs = []
    for index in range(4):
        if index == 1:
            workflow_clock = MutableWorkflowClock(
                digest_clock + timedelta(minutes=1)
            )
        run_worker_cli(
            [
                "run-worker",
                "--env-file",
                str(env_file),
                "--once",
            ],
            worker_module_factory=worker_module_factory,
        )
        captured = capsys.readouterr()
        assert captured.err == ""
        outputs.append(json.loads(captured.out))

    assert [
        [(job["job_kind"], job["state"]) for job in output["jobs"]]
        for output in outputs[:3]
    ] == [
        [
            ("harvest_window", "succeeded"),
            ("explain_snapshot", "succeeded"),
        ],
        [("prepare_digest", "succeeded")],
        [("dispatch_digest", "succeeded")],
    ]
    assert outputs[3]["processed_jobs"] == 0
    assert source.calls == 1
    assert model.schemas == [
        "paper_claims",
        "paper_relevance",
        "reading_card",
        "support_verification",
    ]
    assert len(mail.messages) == 1
    assert mail.messages[0].recipient == "reader@example.com"
    assert "Synthetic arXiv study" in mail.messages[0].text_body
    assert "這份研究提供 arXiv 摘要證據。" in mail.messages[0].text_body

    connection = SqliteConnectionFactory(root).connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM current_summaries"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM digests"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM delivery_outbox"
        ).fetchone()[0] == "provider_accepted"
        assert connection.execute(
            "SELECT count(*) FROM delivery_attempts"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM model_runs"
        ).fetchone()[0] == 4
    finally:
        connection.close()


LIVE_ARXIV_CURSOR = datetime(2017, 6, 12, 0, 0, tzinfo=timezone.utc)


def _discover_recent_live_arxiv_target(
    rate_state: Path,
) -> tuple[str, datetime]:
    window_end = datetime.now(timezone.utc).replace(
        second=0,
        microsecond=0,
    )
    window_start = window_end - timedelta(days=7)
    compiler = ArxivQueryCompilerAdapter()
    query = SourceQueryInput(
        source_id="arxiv",
        profile_id="live_probe",
        profile_revision=1,
        profile_fingerprint="f" * 64,
        domain=DomainQuerySnapshot(
            "machine_learning",
            1,
            ("arxiv",),
            ("cs.LG", "stat.ML"),
        ),
        window_start=window_start,
        window_end=window_end,
        profile_sources=("arxiv",),
        deferred_mode="defer",
        page_size=20,
    )
    plan = compiler.compile(query)
    request = compiler.page(plan)
    fetch = FetchSourcePage(
        ArxivSourceAdapter(
            HttpClientArxivTransportAdapter(
                enabled=True,
                timeout_seconds=30,
            ),
            PosixArxivRateLimitAdapter(rate_state),
            enabled=True,
        )
    )
    fetched = fetch(request)
    if fetched.failure_code is not None or fetched.response is None:
        raise AssertionError(
            "recent live arXiv target discovery failed: "
            + str(fetched.failure_code)
        )
    response = fetched.response
    page = ArxivAtomParserAdapter()(
        request,
        response.body,
        http_status=response.status,
    )
    for record in page.records:
        title = record.title.strip()
        occurrence = (
            record.published_at
            if record.version in {None, 1}
            else record.updated_at
        )
        if (
            title
            and '"' not in title
            and "\\" not in title
            and len(title.encode("utf-8")) <= 512
            and response.received_at - occurrence <= timedelta(days=14)
        ):
            return title, occurrence
    raise AssertionError("no recent safe-title arXiv target found")


def _seed_live_arxiv_mvp(
    root: Path,
    *,
    include_title: str = "Attention Is All You Need",
    cursor_end: datetime | None = LIVE_ARXIV_CURSOR,
) -> None:
    seeds = json.loads(bundled_domain_seeds_json())["domains"]
    raw_definition = next(
        item
        for item in seeds
        if item["id"] == "machine_learning"
    )
    normalized = NormalizeWatchConfiguration(
        frozenset({"arxiv", "pubmed", "crossref"})
    )(
        "domains",
        json.dumps(
            {"domains": [raw_definition]},
            ensure_ascii=False,
        ),
    )
    definition = json.loads(normalized.canonical_json)["domains"][0]
    input_json = (
        None
        if cursor_end is None
        else json.dumps(
            {
                "binding_key": "personal:1:machine_learning:1:arxiv",
                "window_end": cursor_end.isoformat(),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    fingerprint = (
        None
        if input_json is None
        else hashlib.sha256(input_json.encode("utf-8")).hexdigest()
    )
    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
            (
                definition["id"],
                definition["name"],
                json.dumps(
                    definition,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                1,
                NOW.isoformat(),
            ),
        )
        current_filters = json.loads(
            connection.execute(
                "SELECT filters_json FROM watch_profile_revisions "
                "WHERE profile_id='personal' AND revision=1"
            ).fetchone()["filters_json"]
        )
        current_filters["include"] = [include_title]
        connection.execute(
            "UPDATE watch_profile_revisions SET scope_text=?,filters_json=? "
            "WHERE profile_id='personal' AND revision=1",
            (
                "關注機器學習、深度學習與 Transformer 架構。",
                json.dumps(
                    current_filters,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        connection.execute(
            "DELETE FROM watch_profile_domains "
            "WHERE profile_id='personal' AND revision=1"
        )
        connection.execute(
            "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
            ("personal", 1, "machine_learning", 1),
        )
        if cursor_end is not None:
            assert input_json is not None
            assert fingerprint is not None
            connection.execute(
                "INSERT INTO workflow_jobs("
                "id,job_kind,business_key,input_json,input_fingerprint,state,"
                "due_at,lease_owner,lease_until,fencing_token,attempt_count,created_at"
                ") VALUES(?,?,?,?,?,'succeeded',?,NULL,NULL,1,1,?)",
                (
                    "job:live-arxiv-cursor",
                    "harvest_window",
                    "harvest:live-arxiv-cursor",
                    input_json,
                    fingerprint,
                    cursor_end.isoformat(),
                    cursor_end.isoformat(),
                ),
            )
        connection.execute(
            "INSERT INTO delivery_subscriptions("
            "id,reader_id,channel,enabled,timezone,schedule_json,max_items,"
            "recipient_ref,policy_version,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "subscription:live-arxiv",
                "reader:local",
                "email",
                1,
                "UTC",
                '{"kind":"daily","local_time":"08:00"}',
                5,
                "recipient:primary",
                1,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.execute(
            "UPDATE workspace_metadata SET external_effects_enabled=1 "
            "WHERE singleton=1"
        )
        connection.commit()
    finally:
        connection.close()


def test_live_mvp_seed_uses_semantic_machine_learning_domain(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)

    _seed_live_arxiv_mvp(root)

    connection = SqliteConnectionFactory(root).connect()
    try:
        profile = connection.execute(
            "SELECT scope_text FROM watch_profile_revisions "
            "WHERE profile_id='personal' AND revision=1"
        ).fetchone()
        domains = connection.execute(
            "SELECT domain_id FROM watch_profile_domains "
            "WHERE profile_id='personal' AND revision=1"
        ).fetchall()
        definition = connection.execute(
            "SELECT definition_json FROM domain_definitions "
            "WHERE id='machine_learning' AND revision=1"
        ).fetchone()
        filters = connection.execute(
            "SELECT filters_json FROM watch_profile_revisions "
            "WHERE profile_id='personal' AND revision=1"
        ).fetchone()
    finally:
        connection.close()

    assert "機器學習" in profile["scope_text"]
    assert [row["domain_id"] for row in domains] == ["machine_learning"]
    decoded = json.loads(definition["definition_json"])
    assert decoded["id"] == "machine_learning"
    assert decoded["name"] == "機器學習"
    assert decoded["sources"] == ["arxiv", "crossref"]
    assert decoded["source_categories"]["arxiv"] == ["cs.LG", "stat.ML"]
    assert json.loads(filters["filters_json"])["include"] == [
        "Attention Is All You Need"
    ]

    domain = SqliteWatchProfileStoreAdapter(
        SqliteConnectionFactory(root).connect
    ).read_domain("machine_learning", 1)
    assert domain.domain_id == "machine_learning"
    assert domain.name == "機器學習"


def _next_live_digest_clock(observed_at: datetime) -> datetime:
    observed = observed_at.astimezone(timezone.utc)
    cutoff = observed.replace(
        hour=8,
        minute=0,
        second=0,
        microsecond=0,
    )
    if cutoff <= observed:
        cutoff += timedelta(days=1)
    return cutoff + timedelta(minutes=1)


def test_live_digest_clock_uses_first_cutoff_after_observation() -> None:
    before = datetime(2026, 9, 29, 4, 52, tzinfo=timezone.utc)
    after = datetime(2026, 9, 29, 9, 5, tzinfo=timezone.utc)

    assert _next_live_digest_clock(before) == datetime(
        2026,
        9,
        29,
        8,
        1,
        tzinfo=timezone.utc,
    )
    assert _next_live_digest_clock(after) == datetime(
        2026,
        9,
        30,
        8,
        1,
        tzinfo=timezone.utc,
    )


class MutableWorkflowClock:
    def __init__(self, current: datetime | None = None) -> None:
        self.current = current

    def now(self) -> datetime:
        return (
            datetime.now(timezone.utc)
            if self.current is None
            else self.current
        )


def test_mutable_workflow_clock_uses_wall_time_until_pinned() -> None:
    clock = MutableWorkflowClock(
        (target_occurrence + timedelta(hours=1)).replace(
            second=0,
            microsecond=0,
        )
    )
    before = datetime.now(timezone.utc)
    observed = clock.now()
    after = datetime.now(timezone.utc)

    assert before <= observed <= after

    pinned = datetime(2026, 9, 29, 8, 1, tzinfo=timezone.utc)
    clock.current = pinned
    assert clock.now() == pinned


def _mark_live_arxiv_caught_up(
    root: Path,
    window_end: datetime,
) -> None:
    input_json = json.dumps(
        {
            "binding_key": "personal:1:machine_learning:1:arxiv",
            "window_end": window_end.isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(input_json.encode("utf-8")).hexdigest()
    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO workflow_jobs("
            "id,job_kind,business_key,input_json,input_fingerprint,state,"
            "due_at,lease_owner,lease_until,fencing_token,attempt_count,created_at"
            ") VALUES(?,?,?,?,?,'succeeded',?,NULL,NULL,1,1,?)",
            (
                "job:live-arxiv-caught-up",
                "harvest_window",
                "harvest:live-arxiv-caught-up",
                input_json,
                fingerprint,
                window_end.isoformat(),
                window_end.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _required_live_mvp(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise AssertionError(f"missing live MVP env: {name}")
    return value


def test_live_full_mvp_uses_worker_cycles_for_digest_and_dispatch() -> None:
    source = inspect.getsource(test_live_arxiv_openrouter_gmail_e2e)

    assert "PrepareScheduledDigestPort" not in source
    assert "ProcessRevisionNoticePort" not in source
    assert "Attention Is All You Need" not in source
    assert "_discover_recent_live_arxiv_target" in source
    assert source.count("worker(") >= 3


@pytest.mark.live_external
@pytest.mark.skipif(
    os.environ.get("PAPER_RADAR_LIVE_MVP_E2E") != "1",
    reason=(
        "set PAPER_RADAR_LIVE_MVP_E2E=1 for the bounded real "
        "arXiv -> OpenRouter -> Gmail smoke"
    ),
)
def test_live_arxiv_openrouter_gmail_e2e(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    rate_state = tmp_path / "arxiv-rate-live-full-mvp.json"
    target_title, target_occurrence = _discover_recent_live_arxiv_target(
        rate_state
    )
    _seed_live_arxiv_mvp(
        root,
        include_title=target_title,
        cursor_end=None,
    )
    # The probe and the worker share the production local arXiv limiter.
    # Let the probe's lease expire before issuing the exact-title request.
    time.sleep(3.1)

    try:
        smtp_port = int(_required_live_mvp("PAPER_RADAR_SMTP_PORT"))
    except ValueError:
        raise AssertionError("invalid live MVP SMTP port") from None

    period_key, currency = commissioned_openrouter_budget_period()
    recipient = _required_live_mvp("PAPER_RADAR_SMTP_RECIPIENT")
    clock = MutableWorkflowClock()
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(rate_state),
                transport=HttpClientArxivTransportAdapter(
                    enabled=True,
                    timeout_seconds=30,
                ),
                allow_live_model=True,
                model_api_key=_required_live_mvp(
                    "PAPER_RADAR_OPENROUTER_API_KEY"
                ),
                generation_budget_policy=GenerationBudgetPolicy(
                    period_key,
                    currency,
                    2_000_000,
                    commissioned_openrouter_reservation_micros(),
                    commissioned_openrouter_execution_policy_fingerprint(),
                ),
                allow_live_mail=True,
                recipient_map_json=json.dumps(
                    {"recipient:primary": recipient},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                smtp_host=_required_live_mvp("PAPER_RADAR_SMTP_HOST"),
                smtp_port=smtp_port,
                smtp_sender=_required_live_mvp("PAPER_RADAR_SMTP_SENDER"),
                smtp_username=_required_live_mvp(
                    "PAPER_RADAR_SMTP_USERNAME"
                ),
                smtp_password=_required_live_mvp(
                    "PAPER_RADAR_SMTP_PASSWORD"
                ),
                smtp_security=_required_live_mvp(
                    "PAPER_RADAR_SMTP_SECURITY"
                ),
                workflow_clock=clock,
            )
        ],
        auto_bind=False,
    )
    worker = injector.get(RunWorkerCyclePort)

    generated = worker(
        "worker:mvp-live-full",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )

    assert [
        (job.job_kind, job.state, job.error_code)
        for job in generated.jobs
    ] == [
        ("harvest_window", "succeeded", None),
        ("explain_snapshot", "succeeded", None),
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        paper = connection.execute(
            "SELECT e.observed_at,e.event_kind,r.title "
            "FROM research_events e "
            "JOIN paper_revisions r ON r.id=e.revision_id "
            "WHERE r.title=? "
            "ORDER BY e.observed_at DESC LIMIT 1",
            (target_title,),
        ).fetchone()
        assert paper is not None
        assert paper["title"] == target_title
        assert paper["event_kind"] in {
            "new_work",
            "revision_available",
        }
        observed_at = datetime.fromisoformat(paper["observed_at"])

        runs = connection.execute(
            "SELECT task_kind,state,actual_cost_micros "
            "FROM model_runs ORDER BY started_at,id"
        ).fetchall()
        assert [row["task_kind"] for row in runs] == [
            "claim_extraction",
            "relevance_assessment",
            "abstract_reading_card",
            "support_verification",
        ]
        assert all(row["state"] == "succeeded" for row in runs)
        assert all(
            isinstance(row["actual_cost_micros"], int)
            and row["actual_cost_micros"] > 0
            for row in runs
        )

        summary = connection.execute(
            "SELECT language,qa_state FROM summary_revisions"
        ).fetchone()
        assert tuple(summary) == ("zh-TW", "passed")
    finally:
        connection.close()

    observed_at = observed_at.astimezone(timezone.utc)
    digest_clock = _next_live_digest_clock(observed_at)
    _mark_live_arxiv_caught_up(root, digest_clock)
    clock.current = digest_clock

    digested = worker(
        "worker:mvp-live-full",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )
    assert [
        (job.job_kind, job.state, job.error_code)
        for job in digested.jobs
    ] == [
        ("prepare_digest", "succeeded", None),
    ]

    delivered = worker(
        "worker:mvp-live-full",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )
    assert [
        (job.job_kind, job.state, job.error_code)
        for job in delivered.jobs
    ] == [
        ("dispatch_digest", "succeeded", None),
    ]

    replay = worker(
        "worker:mvp-live-full",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )
    assert replay.processed_jobs == 0

    connection = SqliteConnectionFactory(root).connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM digests"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM digest_items"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM delivery_outbox"
        ).fetchone()[0] == "provider_accepted"
        assert connection.execute(
            "SELECT count(*) FROM delivery_attempts"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.live_external
@pytest.mark.skipif(
    os.environ.get("PAPER_RADAR_LIVE_ARXIV") != "1",
    reason="set PAPER_RADAR_LIVE_ARXIV=1 for the bounded public arXiv smoke",
)
def test_live_arxiv_attention_paper_reaches_fake_email(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _seed_live_arxiv_mvp(root)
    model = FakeOpenRouterHttpTransport()
    mail = CapturingMailSender()
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                rate_limit_state=str(tmp_path / "arxiv-rate-live-mvp.json"),
                transport=HttpClientArxivTransportAdapter(
                    enabled=True,
                    timeout_seconds=30,
                ),
                allow_live_model=True,
                model_api_key="sk-or-v1-fake-live-secret-000000",
                model_http_transport=model,
                generation_budget_policy=GenerationBudgetPolicy(
                    datetime.now(timezone.utc).strftime("%Y-%m"),
                    "USD",
                    1_000_000,
                    10_000,
                    "2" * 64,
                ),
                allow_live_mail=True,
                mail_sender=mail,
                recipient_resolver=MvpRecipientResolver(),
            )
        ],
        auto_bind=False,
    )
    worker = injector.get(RunWorkerCyclePort)

    generated = worker(
        "worker:mvp-live-arxiv",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )

    assert [
        (job.job_kind, job.state, job.error_code)
        for job in generated.jobs
    ] == [
        ("harvest_window", "succeeded", None),
        ("explain_snapshot", "succeeded", None),
    ]
    assert model.schemas == [
        "paper_claims",
        "paper_relevance",
        "reading_card",
        "support_verification",
    ]

    connection = SqliteConnectionFactory(root).connect()
    try:
        paper = connection.execute(
            "SELECT e.observed_at,r.title "
            "FROM research_events e "
            "JOIN paper_revisions r ON r.id=e.revision_id "
            "WHERE lower(r.title) LIKE '%attention is all you need%' "
            "ORDER BY e.observed_at DESC LIMIT 1"
        ).fetchone()
        assert paper is not None
        observed_at = datetime.fromisoformat(paper["observed_at"])
    finally:
        connection.close()

    cutoff = datetime.now(timezone.utc)
    assert cutoff >= observed_at
    prepared = injector.get(PrepareScheduledDigestPort)(
        ScheduledDigestRequest(
            "subscription:live-arxiv",
            "live-arxiv-smoke",
            observed_at - timedelta(minutes=1),
            cutoff,
        ),
        created_at=cutoff,
    )
    assert prepared.state == "queued"
    assert prepared.item_count == 1
    assert prepared.outbox_id is not None

    delivered = injector.get(ProcessRevisionNoticePort)(
        RevisionNoticeRequest(prepared.outbox_id)
    )
    assert delivered.state == "succeeded"
    assert len(mail.messages) == 1
    message = mail.messages[0]
    assert message.recipient == "reader@example.com"
    assert "Attention Is All You Need" in message.text_body
    assert "這份研究提供 arXiv 摘要證據。" in message.text_body
