import json
from datetime import datetime, timezone
from pathlib import Path

from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.adapters.driven.sqlite_pubmed_harvest_store_adapter import (
    SqlitePubmedHarvestStoreAdapter,
)
from libs.discovery.application.commands.run_pubmed_harvest_window import (
    RunPubmedHarvestWindow,
)
from libs.discovery.dtos.source_http_response import SourceHttpResponse
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
from libs.research_workflow.adapters.driven.sqlite_scheduler_input_adapter import (
    SqliteSchedulerInputAdapter,
)
from libs.research_workflow.adapters.driven.sqlite_workflow_job_store_adapter import (
    SqliteWorkflowJobStoreAdapter,
)
from libs.research_workflow.application.commands.process_workflow_job import ProcessWorkflowJob
from libs.research_workflow.application.commands.run_scheduler_tick import RunSchedulerTick
from libs.research_workflow.application.commands.run_worker_cycle import RunWorkerCycle
from libs.research_workflow.application.queries.build_harvest_query_input import (
    BuildHarvestQueryInput,
)
from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import (
    SqliteWatchProfileStoreAdapter,
)
from libs.watch_profiles.application.queries.read_domain_definition import ReadDomainDefinition
from libs.watch_profiles.application.queries.read_watch_profile import ReadWatchProfile


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return NOW


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
        <AuthorList><Author><LastName>Lin</LastName><ForeName>Wei</ForeName></Author></AuthorList>
        <Language>eng</Language>
        <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList>
      <ArticleId IdType="pubmed">12345678</ArticleId>
    </ArticleIdList></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""
        else:
            raise AssertionError(request.url)
        return SourceHttpResponse(200, body, (), NOW, None)


def _workspace(tmp_path: Path):
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 10

    raw = SqliteConnectionFactory(root)
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
            ("personal", "reader:local", "mine", "active", None, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
            (
                "personal",
                1,
                "羽球",
                '{"allow_preprints":true,"exclude":[],"free_only":false,"include":[],'
                '"languages":["english"],"sources":["pubmed"]}',
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

    return root, SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=10,
    )


def test_scheduler_worker_pubmed_two_stage_harvest_is_durable_without_network(
    tmp_path: Path,
) -> None:
    root, schema = _workspace(tmp_path)
    profile_store = SqliteWatchProfileStoreAdapter(schema.connect)
    builder = BuildHarvestQueryInput(
        ReadWatchProfile(profile_store),
        ReadDomainDefinition(profile_store),
    )
    raw = SqliteConnectionFactory(root)
    publish = PublishObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(raw),
    )
    transport = FakeNcbiTransport()
    source = PubmedSourceAdapter(
        tool="paper-radar",
        email="reader@example.com",
    )
    pubmed = RunPubmedHarvestWindow(
        source,
        transport,
        publish,
        SqlitePubmedHarvestStoreAdapter(schema.connect),
    )
    jobs = SqliteWorkflowJobStoreAdapter(schema.connect)
    clock = FixedClock()
    cycle = RunWorkerCycle(
        RunSchedulerTick(SqliteSchedulerInputAdapter(schema.connect), jobs),
        ProcessWorkflowJob(
            store=jobs,
            builder=builder,
            harvest=None,
            clock=clock,
            live_source_enabled=True,
            pubmed=pubmed,
        ),
        clock,
    )

    result = cycle(
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

    connection = raw.connect()
    try:
        job = connection.execute(
            "SELECT state,input_json FROM workflow_jobs WHERE job_kind='harvest_window'"
        ).fetchone()
        payload = json.loads(job["input_json"])
        assert job["state"] == "succeeded"
        assert payload["source_id"] == "pubmed"
        assert payload["window_start"] == "2026-09-23T00:00:00+00:00"
        assert payload["window_end"] == "2026-09-24T00:00:00+00:00"

        unit = connection.execute(
            "SELECT state,cursor_json,coverage_json,checkpoint_version FROM harvest_units"
        ).fetchone()
        assert unit["state"] == "succeeded"
        assert json.loads(unit["cursor_json"]) == {
            "format_version": 1,
            "next_start": 1,
            "total_results": 1,
        }
        assert json.loads(unit["coverage_json"]) == {
            "format_version": 1,
            "record_count": 1,
            "complete": True,
        }
        assert unit["checkpoint_version"] == 1

        observation = connection.execute(
            "SELECT native_id,payload_object_id,parser_version FROM source_observations"
        ).fetchone()
        assert observation["native_id"] == "12345678"
        assert observation["parser_version"] == "pubmed-eutils-parser-v1"
        assert observation["payload_object_id"].startswith("raw:")
        raw_rows = connection.execute(
            "SELECT count(*) FROM object_registry WHERE kind='raw'"
        ).fetchone()[0]
        assert raw_rows == 2
    finally:
        connection.close()
