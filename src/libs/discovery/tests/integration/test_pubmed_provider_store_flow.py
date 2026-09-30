"""Real provider compile/parse to SQLite; responses are fixed offline fixtures."""

import json

import pytest

from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.exceptions.source_parse_error import SourceParseError
from libs.discovery.tests.integration.test_pubmed_checkpoint_integrity import NOW, publish, sql
from libs.discovery.tests.integration.test_pubmed_checkpoint_integrity import workspace as workspace
from libs.discovery.tests.integration.test_pubmed_durable_harvest import query


def search_response(pmids):
    return json.dumps({"esearchresult": {
        "count": str(len(pmids)), "retmax": str(len(pmids)), "retstart": "0", "idlist": list(pmids),
    }}).encode()


def efetch_response(pmids):
    articles = "".join(
        f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>"
        f"<ArticleTitle>Paper {pmid}</ArticleTitle><Abstract><AbstractText>"
        "Badminton study.</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle>"
        for pmid in pmids
    )
    return f"<PubmedArticleSet>{articles}</PubmedArticleSet>".encode()


def parsed_search(path, store, pmids):
    source = PubmedSourceAdapter(tool="paper-radar", email="fixture@example.com")
    definition = source.compile(query())
    store.ensure(definition, NOW)
    request = source.page(definition)
    raw = search_response(pmids)
    receipt = source.parse_search(request, raw, http_status=200)
    store.save_search(definition, receipt, publish(path, raw), NOW)
    return source, definition


def test_real_provider_parsers_commit_each_bibliography_batch_only(workspace):
    path, store = workspace
    source, definition = parsed_search(path, store, ("100", "200"))
    assert sql(path, "SELECT COUNT(*) FROM source_observations") == [(0,)]
    first = store.next_batch(definition, maximum_batch_size=1)
    raw = efetch_response(first.pmids)
    receipt = source.parse_bibliography(source.bibliography_request(first.pmids), raw, http_status=200)
    partial = store.save_bibliography(definition, first, receipt, publish(path, raw), NOW)
    assert (partial.next_start, partial.next_batch_offset) == (0, 1)
    second = store.next_batch(definition, maximum_batch_size=1)
    raw = efetch_response(second.pmids)
    receipt = source.parse_bibliography(source.bibliography_request(second.pmids), raw, http_status=200)
    final = store.save_bibliography(definition, second, receipt, publish(path, raw), NOW)
    assert (final.state, final.next_start, final.checkpoint_version) == ("succeeded", 2, 1)
    assert sql(path, "SELECT native_id FROM source_observations ORDER BY native_id") == [("100",), ("200",)]


def test_zero_search_response_completes_without_bibliography(workspace):
    path, store = workspace
    _, definition = parsed_search(path, store, ())
    assert store.read(definition).state == "verified_empty"
    assert store.next_batch(definition, maximum_batch_size=100) is None
    assert sql(path, "SELECT COUNT(*) FROM pubmed_bibliography_batches") == [(0,)]
    assert sql(path, "SELECT COUNT(*) FROM source_observations") == [(0,)]


def test_efetch_missing_pmid_keeps_search_page_retryable(workspace):
    path, store = workspace
    source, definition = parsed_search(path, store, ("100", "200"))
    before = store.read(definition)
    pending = store.next_batch(definition, maximum_batch_size=2)
    request = source.bibliography_request(pending.pmids)
    with pytest.raises(SourceParseError, match="pubmed_batch_identity_mismatch"):
        source.parse_bibliography(request, efetch_response(("100",)), http_status=200)
    assert store.read(definition) == before
    assert sql(path, "SELECT COUNT(*) FROM source_observations") == [(0,)]
    raw = efetch_response(pending.pmids)
    receipt = source.parse_bibliography(request, raw, http_status=200)
    assert store.save_bibliography(definition, pending, receipt, publish(path, raw), NOW).state == "succeeded"
