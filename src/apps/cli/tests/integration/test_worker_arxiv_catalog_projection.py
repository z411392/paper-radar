import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from injector import Injector

from apps.cli.module import WorkerCliModule
from libs.paper_explanations.dtos.generation_budget_policy import (
    GenerationBudgetPolicy,
)
from libs.paper_explanations.dtos.structured_generation_result import (
    GenerationReceipt,
    StructuredGenerationResult,
)
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import (
    SqliteConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.research_workflow.ports.run_worker_cycle_port import RunWorkerCyclePort


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
                        "text": "這份 arXiv 摘要提供研究證據。",
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
