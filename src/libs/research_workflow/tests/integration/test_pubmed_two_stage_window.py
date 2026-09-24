import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.adapters.driven.sqlite_pubmed_window_store_adapter import (
    SqlitePubmedWindowStoreAdapter,
)
from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.application.commands.publish_object import PublishObject
from libs.research_workflow.application.commands.run_pubmed_window import RunPubmedWindow


NOW = datetime(2026, 9, 24, 0, 5, tzinfo=timezone.utc)
START = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


class Clock:
    def now(self):
        return NOW


class Fetcher:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def fetch(self, request):
        self.calls.append(request.url)
        if not self.steps:
            raise AssertionError("unexpected provider request")
        kind, value = self.steps.pop(0)
        if kind == "search":
            assert "esearch.fcgi" in request.url
        elif kind == "fetch":
            assert "efetch.fcgi" in request.url
        else:
            raise AssertionError(kind)
        return value


class Catalog:
    def __init__(self):
        self.values = []

    def __call__(self, observation):
        self.values.append(observation)
        return object()


def _success(body: bytes) -> SourceFetchResult:
    response = SourceHttpResponse(
        200,
        body,
        (("content-type", "application/xml"),),
        NOW,
        None,
    )
    return SourceFetchResult(
        "placeholder",
        response,
        hashlib.sha256(body).hexdigest(),
        None,
        False,
        None,
    )


def _with_request_fingerprint(result: SourceFetchResult, fingerprint: str) -> SourceFetchResult:
    return SourceFetchResult(
        fingerprint,
        result.response,
        result.response_sha256,
        result.failure_code,
        result.retryable,
        result.retry_after_seconds,
    )


def _search_body() -> bytes:
    return json.dumps(
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


def _bibliography_body() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE">
      <PMID Version="1">12345678</PMID>
      <Article>
        <Journal>
          <JournalIssue><PubDate><Year>2026</Year><Month>Sep</Month><Day>20</Day></PubDate></JournalIssue>
          <Title>Sports Science</Title>
        </Journal>
        <ArticleTitle>Badminton biomechanics</ArticleTitle>
        <Abstract><AbstractText>Result text.</AbstractText></Abstract>
        <AuthorList><Author><LastName>Lin</LastName><ForeName>Wei</ForeName></Author></AuthorList>
        <Language>eng</Language>
        <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData><ArticleIdList>
      <ArticleId IdType="pubmed">12345678</ArticleId>
      <ArticleId IdType="pmc">PMC9999999</ArticleId>
    </ArticleIdList></PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""


def _query() -> SourceQueryInput:
    return SourceQueryInput(
        source_id="pubmed",
        profile_id="personal",
        profile_revision=1,
        profile_fingerprint="a" * 64,
        domain=DomainQuerySnapshot(
            "badminton",
            1,
            ("pubmed",),
            (),
            ("badminton",),
            (),
            (),
        ),
        window_start=START,
        window_end=END,
        profile_sources=("pubmed",),
        deferred_mode="defer",
        time_basis="createDate",
        page_size=100,
    )


def _runtime(tmp_path: Path):
    root = tmp_path / "workspace"
    info = SqliteWorkspaceBootstrapAdapter(
        root,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    assert info.schema_version == 10
    raw = SqliteConnectionFactory(root)
    publish = PublishObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(raw),
    )
    store = SqlitePubmedWindowStoreAdapter(raw.connect)
    return raw, publish, store


def _runner(fetcher, publish, store, catalog):
    return RunPubmedWindow(
        source=PubmedSourceAdapter(tool="paper-radar", email="reader@example.com"),
        fetcher=fetcher,
        objects=publish,
        store=store,
        catalog=catalog,
        clock=Clock(),
    )


def test_efetch_failure_reuses_durable_search_page_on_retry(tmp_path: Path) -> None:
    raw, publish, store = _runtime(tmp_path)
    source = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    plan = source.compile(_query())
    search_request = source.page(plan, 0)
    bib_request = source.bibliography_request(("12345678",))
    search = _with_request_fingerprint(_success(_search_body()), search_request.request_fingerprint)
    timeout = SourceFetchResult(
        bib_request.request_fingerprint,
        None,
        None,
        "source_timeout",
        True,
        1.0,
    )
    first_fetcher = Fetcher([("search", search), ("fetch", timeout)])
    catalog = Catalog()

    with pytest.raises(SourceFetchError, match="source_timeout"):
        _runner(first_fetcher, publish, store, catalog)(
            "harvest:pubmed:test",
            _query(),
        )

    progress = store.read("harvest:pubmed:test")
    assert progress.next_start == 0
    assert progress.total_results == 1
    assert progress.pending_page is not None
    assert progress.pending_page.pmids == ("12345678",)
    assert catalog.values == []

    bibliography = _with_request_fingerprint(
        _success(_bibliography_body()),
        bib_request.request_fingerprint,
    )
    retry_fetcher = Fetcher([("fetch", bibliography)])
    result = _runner(retry_fetcher, publish, store, catalog)(
        "harvest:pubmed:test",
        _query(),
    )

    assert result.state == "succeeded"
    assert result.next_start == 1
    assert result.total_results == 1
    assert len(retry_fetcher.calls) == 1
    assert "efetch.fcgi" in retry_fetcher.calls[0]
    assert len(catalog.values) == 1
    assert catalog.values[0].source_observation_id.startswith("pubmed-observation:")
    assert catalog.values[0].identifier_namespace == "pmid"
    assert catalog.values[0].identifier_value == "12345678"

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM pubmed_search_pages"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM pubmed_bibliography_observations"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_zero_result_window_is_verified_without_efetch(tmp_path: Path) -> None:
    _, publish, store = _runtime(tmp_path)
    source = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    plan = source.compile(_query())
    request = source.page(plan, 0)
    body = json.dumps(
        {
            "header": {"type": "esearch", "version": "0.3"},
            "esearchresult": {
                "count": "0",
                "retmax": "0",
                "retstart": "0",
                "idlist": [],
                "translationset": [],
                "querytranslation": "badminton[Title/Abstract]",
            },
        },
        separators=(",", ":"),
    ).encode()
    fetcher = Fetcher([
        ("search", _with_request_fingerprint(_success(body), request.request_fingerprint))
    ])

    result = _runner(fetcher, publish, store, Catalog())(
        "harvest:pubmed:empty",
        _query(),
    )

    assert result.state == "verified_empty"
    assert result.total_results == 0
    assert len(fetcher.calls) == 1
