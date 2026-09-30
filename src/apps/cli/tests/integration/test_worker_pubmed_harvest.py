import json
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


class FakeNcbiTransport:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, request):
        self.urls.append(request.url)
        if "esearch.fcgi" in request.url:
            body = json.dumps(
                {
                    "header": {"type": "esearch", "version": "0.3"},
                    "esearchresult": {
                        "count": "1",
                        "retmax": "1",
                        "retstart": "0",
                        "idlist": ["12345678"],
                        "translationset": [],
                        "querytranslation": "badminton[Title/Abstract]",
                    },
                },
                separators=(",", ":"),
            ).encode()
        elif "efetch.fcgi" in request.url:
            body = b"""<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE">
      <PMID Version="1">12345678</PMID>
      <Article>
        <Journal>
          <JournalIssue CitedMedium="Internet">
            <PubDate><Year>2026</Year><Month>Sep</Month><Day>23</Day></PubDate>
          </JournalIssue>
          <Title>Sports Science</Title>
        </Journal>
        <ArticleTitle>Badminton training study</ArticleTitle>
        <Abstract><AbstractText>Training result.</AbstractText></Abstract>
        <AuthorList>
          <Author><LastName>Lin</LastName><ForeName>Wei</ForeName></Author>
        </AuthorList>
        <Language>eng</Language>
        <PublicationTypeList>
          <PublicationType>Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345678</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""
        else:
            raise AssertionError(request.url)
        return SourceHttpResponse(200, body, (), NOW, None)


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
            ("personal", "reader:local", "mine", "active", None, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
            (
                "personal",
                1,
                "羽球",
                '{"allow_preprints":true,"exclude":[],"free_only":false,'
                '"include":[],"languages":["english"],"sources":["pubmed"]}',
                "a" * 64,
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
        connection.commit()
    finally:
        connection.close()
    return root


def test_worker_pubmed_harvest_projects_catalog_event_and_abstract_evidence(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path)
    transport = FakeNcbiTransport()
    rate_state = tmp_path / "ncbi-rate.json"
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_source=True,
                ncbi_email="reader@example.com",
                ncbi_rate_limit_state=str(rate_state),
                ncbi_transport=transport,
            )
        ],
        auto_bind=False,
    )

    result = injector.get(RunWorkerCyclePort)(
        "worker:pubmed",
        max_new_jobs=10,
        max_jobs=10,
        lease_seconds=300,
    )

    assert result.scheduler.new_jobs == 1
    assert result.scheduler.coverage_gaps == ()
    assert result.processed_jobs == 1
    assert result.jobs[0].job_kind == "harvest_window"
    assert result.jobs[0].state == "succeeded"
    assert len(transport.urls) == 2
    assert "esearch.fcgi" in transport.urls[0]
    assert "efetch.fcgi" in transport.urls[1]

    connection = SqliteConnectionFactory(root).connect()
    try:
        job = connection.execute(
            "SELECT state,input_json FROM workflow_jobs "
            "WHERE job_kind='harvest_window'"
        ).fetchone()
        payload = json.loads(job["input_json"])
        assert job["state"] == "succeeded"
        assert payload["source_id"] == "pubmed"

        unit = connection.execute(
            "SELECT id,state,checkpoint_version FROM harvest_units"
        ).fetchone()
        assert unit["state"] == "succeeded"
        assert unit["checkpoint_version"] == 1

        observation = connection.execute(
            "SELECT id,native_id,payload_object_id,parser_version "
            "FROM source_observations"
        ).fetchone()
        assert observation["native_id"] == "12345678"
        assert observation["parser_version"] == "pubmed-eutils-parser-v1"
        assert observation["payload_object_id"].startswith("raw:")

        manifestation = connection.execute(
            "SELECT source_namespace,native_id,manifestation_kind "
            "FROM paper_manifestations"
        ).fetchone()
        assert tuple(manifestation) == ("pmid", "12345678", "publication")

        revision = connection.execute(
            "SELECT id,title FROM paper_revisions"
        ).fetchone()
        assert revision["title"] == "Badminton training study"

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
        assert snapshot["parser_version"] == "pubmed-eutils-parser-v1:abstract-v1"
        assert connection.execute(
            "SELECT count(*) FROM evidence_anchors"
        ).fetchone()[0] == 1

        progress = connection.execute(
            "SELECT unit_id,source,projected_count,state "
            "FROM source_catalog_projection_progress"
        ).fetchone()
        assert tuple(progress) == (
            unit["id"],
            "pubmed",
            1,
            "succeeded",
        )
    finally:
        connection.close()
