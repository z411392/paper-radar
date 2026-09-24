import json
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.source_parse_error import SourceParseError
from libs.scholarly_catalog.adapters.driven.pmc_full_text_adapter import PmcFullTextAdapter


START = datetime(2026, 9, 23, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


def query() -> SourceQueryInput:
    return SourceQueryInput(
        source_id="pubmed",
        profile_id="personal",
        profile_revision=3,
        profile_fingerprint="a" * 64,
        domain=DomainQuerySnapshot(
            "badminton",
            1,
            ("pubmed", "crossref"),
            (),
            ("badminton",),
            ("racket sport",),
            ("tennis",),
        ),
        window_start=START,
        window_end=END,
        profile_sources=("pubmed",),
        profile_include=("athlete",),
        profile_exclude=("mouse",),
        languages=("english",),
        free_only=True,
        allow_preprints=True,
        scope_text="羽球研究",
        deferred_mode="defer",
        time_basis="createDate",
        page_size=100,
    )


def test_pubmed_compile_uses_create_date_window_and_defers_access_policy() -> None:
    adapter = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")

    plan = adapter.compile(query())
    request = adapter.page(plan, 0)
    parsed = urlsplit(request.url)
    params = parse_qs(parsed.query)

    assert plan.source_id == "pubmed"
    assert "[Title/Abstract]" in plan.search_query
    assert "tennis" in plan.search_query
    assert "NOT" in plan.search_query
    assert parsed.netloc == "eutils.ncbi.nlm.nih.gov"
    assert parsed.path.endswith("/esearch.fcgi")
    assert params["db"] == ["pubmed"]
    assert params["datetype"] == ["crdt"]
    assert params["mindate"] == ["2026/09/23"]
    assert params["maxdate"] == ["2026/09/24"]
    assert params["tool"] == ["paper-radar"]
    assert params["email"] == ["reader@example.com"]
    assert {item.name for item in plan.deferred_filters} >= {
        "free_only",
        "scope_text",
    }


def test_pubmed_rejects_publication_date_as_incremental_cursor() -> None:
    adapter = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")

    with pytest.raises(Exception, match="unsupported_time_basis"):
        adapter.compile(replace(query(), time_basis="publicationDate"))


def test_pubmed_search_page_is_bounded_and_deduplicated() -> None:
    adapter = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    plan = adapter.compile(query())
    request = adapter.page(plan, 0)
    body = json.dumps(
        {
            "header": {"type": "esearch", "version": "0.3"},
            "esearchresult": {
                "count": "2",
                "retmax": "2",
                "retstart": "0",
                "idlist": ["12345678", "23456789"],
                "translationset": [],
                "querytranslation": "badminton[Title/Abstract]",
            },
        },
        separators=(",", ":"),
    ).encode()

    page = adapter.parse_search(request, body, http_status=200)

    assert page.pmids == ("12345678", "23456789")
    assert page.observation.total_results == 2
    assert page.observation.record_ids == page.pmids

    duplicate = body.replace(b'"23456789"', b'"12345678"')
    with pytest.raises(SourceParseError, match="duplicate_page_identity"):
        adapter.parse_search(request, duplicate, http_status=200)


def test_pubmed_efetch_parses_bibliography_but_pmc_remains_separate() -> None:
    adapter = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    request = adapter.bibliography_request(("12345678",))
    body = b"""<?xml version="1.0" encoding="UTF-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE">
      <PMID Version="1">12345678</PMID>
      <Article>
        <Journal>
          <JournalIssue CitedMedium="Internet"><PubDate><Year>2026</Year><Month>Sep</Month><Day>20</Day></PubDate></JournalIssue>
          <Title>Sports Science</Title>
        </Journal>
        <ArticleTitle>Badminton biomechanics</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Background text.</AbstractText>
          <AbstractText Label="RESULTS">Result text.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author><LastName>Lin</LastName><ForeName>Wei</ForeName></Author>
        </AuthorList>
        <Language>eng</Language>
        <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345678</ArticleId>
        <ArticleId IdType="doi">10.1000/Test.DOI</ArticleId>
        <ArticleId IdType="pmc">PMC9999999</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""

    batch = adapter.parse_bibliography(request, body, http_status=200)
    record = batch.records[0]

    assert record.pmid == "12345678"
    assert record.title == "Badminton biomechanics"
    assert record.abstract == "BACKGROUND: Background text.\nRESULTS: Result text."
    assert record.authors == ("Lin Wei",)
    assert record.doi == "10.1000/test.doi"
    assert record.pmcid == "PMC9999999"

    pmc = PmcFullTextAdapter().parse(
        PmcFullTextAdapter().request("PMC9999999"),
        b"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-09-24T00:00:00Z</responseDate>
  <request verb="GetRecord">https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/</request>
  <error code="idDoesNotExist">No matching identifier</error>
</OAI-PMH>""",
        http_status=200,
    )
    assert pmc.available is False
    assert pmc.failure_code == "pmc_full_text_unavailable"


def test_pmc_oai_full_text_requires_exact_identity_and_keeps_license_evidence() -> None:
    adapter = PmcFullTextAdapter()
    request = adapter.request("PMC9999999")
    body = b"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"
         xmlns:xlink="http://www.w3.org/1999/xlink">
  <responseDate>2026-09-24T00:00:00Z</responseDate>
  <request verb="GetRecord" identifier="oai:pubmedcentral.nih.gov:PMC9999999" metadataPrefix="pmc">https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/</request>
  <GetRecord><record>
    <header>
      <identifier>oai:pubmedcentral.nih.gov:PMC9999999</identifier>
      <datestamp>2026-09-23</datestamp>
      <setSpec>pmc-open</setSpec>
    </header>
    <metadata>
      <article article-type="research-article">
        <front><article-meta>
          <article-id pub-id-type="pmc">PMC9999999</article-id>
          <permissions>
            <license xlink:href="https://creativecommons.org/licenses/by/4.0/">
              <license-p>Creative Commons Attribution 4.0 International</license-p>
            </license>
          </permissions>
        </article-meta></front>
        <body><sec><title>Results</title><p>Example full text.</p></sec></body>
      </article>
    </metadata>
  </record></GetRecord>
</OAI-PMH>"""

    result = adapter.parse(request, body, http_status=200)

    assert result.available is True
    assert result.automated_retrieval == "permitted"
    assert result.pmcid == "PMC9999999"
    assert result.license_url == "https://creativecommons.org/licenses/by/4.0/"
    assert result.license_text == "Creative Commons Attribution 4.0 International"
    assert result.usage_class == "cc-by"
    assert result.full_text_xml == body

    mismatch = body.replace(b"PMC9999999", b"PMC8888888")
    with pytest.raises(Exception, match="pmc_identity_mismatch"):
        adapter.parse(request, mismatch, http_status=200)


def test_pmc_unknown_license_is_not_promoted_to_unrestricted_reuse() -> None:
    adapter = PmcFullTextAdapter()
    request = adapter.request("PMC9999999")
    body = b"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <GetRecord><record>
    <header>
      <identifier>oai:pubmedcentral.nih.gov:PMC9999999</identifier>
      <setSpec>pmc-open</setSpec>
    </header>
    <metadata><article><front><article-meta><permissions>
      <license><license-p>Publisher-specific terms apply.</license-p></license>
    </permissions></article-meta></front><body><p>Text</p></body></article></metadata>
  </record></GetRecord>
</OAI-PMH>"""

    result = adapter.parse(request, body, http_status=200)

    assert result.available is True
    assert result.automated_retrieval == "permitted"
    assert result.usage_class == "unknown"
    assert result.license_url is None
