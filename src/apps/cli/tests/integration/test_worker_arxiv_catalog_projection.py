from datetime import datetime, timezone
from pathlib import Path

from injector import Injector

from apps.cli.module import WorkerCliModule
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
