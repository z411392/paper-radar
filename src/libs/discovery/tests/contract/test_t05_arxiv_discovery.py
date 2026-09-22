"""Synthetic Atom fixtures only: never contact arXiv or identify real papers."""

import hashlib
import socket
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta
from xml.etree import ElementTree as ET

import pytest

from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import ArxivAtomParserAdapter
from libs.discovery.application.queries.parse_source_page import ParseSourcePage
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_parse_error import SourceParseError

ATOM = "{http://www.w3.org/2005/Atom}"
SEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
ARXIV = "{http://arxiv.org/schemas/atom}"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Atom parsing must not open a socket")

    monkeypatch.setattr(socket, "socket", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


@pytest.fixture
def request_page():
    return SourcePageRequest(
        "arxiv",
        "a" * 64,
        "b" * 64,
        "GET",
        "https://export.arxiv.org/api/query?search_query=ti:synthetic",
        0,
        2,
        30000,
    )


@pytest.fixture
def parse():
    return ParseSourcePage(ArxivAtomParserAdapter())


def atom(ids=("2609.00001v2",), *, start=0, total=None, size=2, modify=None):
    root = ET.Element(ATOM + "feed")
    for name, value in [("id", "https://arxiv.org/api/synthetic"), ("updated", "2026-09-23T00:00:00Z")]:
        ET.SubElement(root, ATOM + name).text = value
    for name, value in [
        ("totalResults", len(ids) if total is None else total),
        ("startIndex", start),
        ("itemsPerPage", size),
    ]:
        ET.SubElement(root, SEARCH + name).text = str(value)
    for identity in ids:
        entry = ET.SubElement(root, ATOM + "entry")
        for name, value in [
            ("id", "https://arxiv.org/abs/" + identity),
            ("title", "Synthetic 羽球 study: 0.42 m ≠ 0.58 m"),
            ("summary", "  合成研究 only.\n180 clips; 0.5 s; no clinical claim.  "),
            ("published", "2026-09-20T09:00:00+08:00"),
            ("updated", "2026-09-22T12:00:00Z"),
        ]:
            ET.SubElement(entry, ATOM + name).text = value
        for name in ["Synthetic Alpha", "Synthetic Beta"]:
            author = ET.SubElement(entry, ATOM + "author")
            ET.SubElement(author, ATOM + "name").text = name
            ET.SubElement(author, ARXIV + "affiliation").text = "Synthetic Lab"
        ET.SubElement(entry, ATOM + "category", term="cs.LG", scheme="http://arxiv.org/schemas/atom")
        ET.SubElement(entry, ATOM + "category", term="I.2.6")
        ET.SubElement(entry, ARXIV + "primary_category", term="cs.LG")
        ET.SubElement(entry, ARXIV + "doi").text = "10.0000/synthetic-fixture"
        ET.SubElement(entry, ARXIV + "journal_ref").text = "Synthetic venue; not verification"
        ET.SubElement(entry, ATOM + "link", rel="related", href="http://127.0.0.1/do-not-fetch")
    if modify:
        modify(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def first(root):
    return root.find(ATOM + "entry")


def test_fields_provenance_dates_authors_and_untrusted_links(parse, request_page):
    raw = atom()
    result = parse(request_page, raw, http_status=200)
    record = result.records[0]
    assert result.raw_body is raw
    assert result.response_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.request_fingerprint == request_page.request_fingerprint
    assert result.parser_version == "arxiv-atom-v1"
    assert record.source_record_id == "2609.00001v2"
    assert record.arxiv_id == "2609.00001" and record.version == 2
    assert record.title == "Synthetic 羽球 study: 0.42 m ≠ 0.58 m"
    assert record.abstract == "  合成研究 only.\n180 clips; 0.5 s; no clinical claim.  "
    assert [author[0] for author in record.authors] == ["Synthetic Alpha", "Synthetic Beta"]
    assert record.authors[0][1] == ("Synthetic Lab",)
    assert record.published_at.utcoffset() == timedelta(0)
    assert record.published_at.hour == 1
    assert record.updated_at.day == 22
    assert record.categories[1] == ("I.2.6", None)
    assert record.primary_category == "cs.LG"
    assert record.doi == "10.0000/synthetic-fixture"
    assert record.journal_reference.startswith("Synthetic")
    assert record.links[0][0] == "http://127.0.0.1/do-not-fetch"
    assert not hasattr(record, "peer_reviewed")
    assert result.observation.record_ids == ("2609.00001v2",)
    with pytest.raises(FrozenInstanceError):
        record.version = 7


@pytest.mark.parametrize(
    "identity,version",
    [("2609.00001", None), ("hep-ex/0307015", None), ("math.GT/0307015v12", 12), ("0704.0001v1", 1)],
)
def test_modern_legacy_and_unknown_version(parse, request_page, identity, version):
    record = parse(request_page, atom((identity,)), http_status=200).records[0]
    assert record.source_record_id == identity
    assert record.version == version


def test_missing_abstract_is_unknown_not_fabricated(parse, request_page):
    raw = atom(modify=lambda r: first(r).remove(first(r).find(ATOM + "summary")))
    record = parse(request_page, raw, http_status=200).records[0]
    assert record.abstract is None
    assert "abstract" in record.missing_fields


def test_empty_abstract_is_distinct_from_absence(parse, request_page):
    raw = atom(modify=lambda r: setattr(first(r).find(ATOM + "summary"), "text", ""))
    record = parse(request_page, raw, http_status=200).records[0]
    assert record.abstract == ""
    assert "abstract" not in record.missing_fields


def test_namespaces_not_prefixes_and_extension_retained(parse, request_page):
    raw = atom(modify=lambda r: ET.SubElement(first(r), "{urn:future}metadata", value="extension"))
    result = parse(request_page, raw, http_status=200)
    assert b"extension" in result.raw_body
    assert result.records[0].version == 2


def test_empty_and_final_short_page_are_valid_shapes(parse, request_page):
    result = parse(request_page, atom((), total=0), http_status=200)
    assert result.observation.total_results == 0 and not result.records
    request_page = replace(request_page, start=2)
    result = parse(request_page, atom(start=2, total=3), http_status=200)
    assert result.observation.start_index == 2 and len(result.records) == 1


def test_replay_is_identical_without_persistence(parse, request_page):
    raw = atom()
    assert parse(request_page, raw, http_status=200) == parse(request_page, raw, http_status=200)


@pytest.mark.parametrize("status", [True, 206, 304, 400, 401, 403, 404, 429, 500])
def test_http_failure_or_partial_never_becomes_empty(parse, request_page, status):
    with pytest.raises(SourceParseError, match="http_response_not_successful"):
        parse(request_page, atom(()), http_status=status)


def test_api_error_entry_is_not_a_paper_or_empty(parse, request_page):
    def mutate(root):
        first(root).find(ATOM + "id").text = "http://arxiv.org/api/errors#private-error-message"

    raw = atom(modify=mutate)
    with pytest.raises(SourceParseError, match="arxiv_api_error") as caught:
        parse(request_page, raw, http_status=200)
    assert "private-error-message" not in str(caught.value)
    assert caught.value.response_sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("tag", ["id", "title", "published", "updated", "author", "category"])
def test_missing_required_entry_fields_fail(parse, request_page, tag):
    def mutate(root):
        for item in first(root).findall(ATOM + tag):
            first(root).remove(item)

    with pytest.raises(SourceParseError):
        parse(request_page, atom(modify=mutate), http_status=200)


@pytest.mark.parametrize("tag", ["id", "title", "published", "updated", "summary"])
def test_duplicate_singleton_fields_fail(parse, request_page, tag):
    with pytest.raises(SourceParseError, match="duplicate_field"):
        parse(request_page, atom(modify=lambda r: ET.SubElement(first(r), ATOM + tag)), http_status=200)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": 1},
        {"total": -1},
        {"total": "NaN"},
        {"size": 0},
        {"size": 3},
        {"total": 30001},
        {"total": 0},
        {"total": 3},
    ],
)
def test_bad_or_incomplete_page_shape_rejected(parse, request_page, kwargs):
    with pytest.raises(SourceParseError):
        parse(request_page, atom(**kwargs), http_status=200)


def test_duplicate_record_identity_rejected(parse, request_page):
    with pytest.raises(SourceParseError, match="duplicate_page_identity"):
        parse(request_page, atom(("2609.00001v1", "2609.00001v1")), http_status=200)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/abs/2609.00001v1",
        "https://arxiv.org.evil/abs/2609.00001",
        "https://user@arxiv.org/abs/2609.00001",
        "https://arxiv.org:443/abs/2609.00001",
        "https://arxiv.org/abs/2609.00001?next=evil",
        "https://arxiv.org/abs/2609.00001#v2",
        "https://arxiv.org/abs/../secret",
        "https://arxiv.org/abs/2613.00001",
        "https://arxiv.org/abs/2609.00001v0",
        "https://arxiv.org/abs/%32%36%30%39.00001",
        "https://arxiv.org/abs/2609.00001v9999999999999999999",
    ],
)
def test_invalid_identity_urls_rejected(parse, request_page, url):
    raw = atom(modify=lambda r: setattr(first(r).find(ATOM + "id"), "text", url))
    with pytest.raises(SourceParseError, match="invalid_source_identity"):
        parse(request_page, raw, http_status=200)


@pytest.mark.parametrize("date", ["2026-09-23", "2026-09-23T00:00:00", "2026-02-30T00:00:00Z", "NaN"])
def test_timestamps_require_valid_explicit_timezone(parse, request_page, date):
    raw = atom(modify=lambda r: setattr(first(r).find(ATOM + "published"), "text", date))
    with pytest.raises(SourceParseError, match="invalid_timestamp"):
        parse(request_page, raw, http_status=200)


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"<feed/>", "invalid_feed_namespace"),
        (b"<", "malformed_xml"),
        (b"\xff", "invalid_utf8"),
        (b"<!DOCTYPE feed [<!ENTITY x 'EXPANSION'>]><feed>&x;</feed>", "xml_doctype_forbidden"),
        (b"<!DOCTYPE feed SYSTEM 'file:///etc/passwd'><feed/>", "xml_doctype_forbidden"),
        (("<x>" * 34 + "</x>" * 34).encode(), "xml_depth_limit"),
        (("<x>" + "<y/>" * 100000 + "</x>").encode(), "xml_node_limit"),
        (b"x" * 8000001, "response_too_large"),
        ("<feed/>".encode("utf-16"), "invalid_utf8"),
    ],
    ids=["namespace", "malformed", "utf8", "internal-dtd", "external-dtd", "depth", "nodes", "size", "utf16"],
)
def test_xml_resource_and_encoding_guards(parse, request_page, raw, code):
    with pytest.raises(SourceParseError, match=code):
        parse(request_page, raw, http_status=200)


def test_non_bytes_input_rejected(parse, request_page):
    with pytest.raises(SourceParseError, match="invalid_response_bytes"):
        parse(request_page, "<feed/>", http_status=200)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_id": "pubmed"},
        {"query_fingerprint": "bad"},
        {"start": True},
        {"max_results": 0},
        {"method": "POST"},
        {"start": -1},
    ],
)
def test_invalid_request_is_not_silently_coerced(parse, request_page, kwargs):
    with pytest.raises(SourceParseError, match="invalid_page_request"):
        parse(replace(request_page, **kwargs), atom(), http_status=200)


def test_missing_pagination_is_not_zero(parse, request_page):
    raw = atom(modify=lambda r: r.remove(r.find(SEARCH + "totalResults")))
    with pytest.raises(SourceParseError, match="missing_field"):
        parse(request_page, raw, http_status=200)


def test_mixed_content_not_flattened_into_claim(parse, request_page):
    raw = atom(modify=lambda r: ET.SubElement(first(r).find(ATOM + "summary"), ATOM + "script"))
    with pytest.raises(SourceParseError, match="unsupported_text_markup"):
        parse(request_page, raw, http_status=200)


def test_url_parser_must_not_silently_strip_control_character(parse, request_page):
    raw = atom(
        modify=lambda r: setattr(first(r).find(ATOM + "id"), "text", "https://arxiv.org/abs/2609.\n00001v2")
    )
    with pytest.raises(SourceParseError, match="invalid_source_identity"):
        parse(request_page, raw, http_status=200)


def test_date_parser_must_not_normalize_invalid_timezone_minutes(parse, request_page):
    raw = atom(
        modify=lambda r: setattr(first(r).find(ATOM + "published"), "text", "2026-09-23T00:00:00+00:60")
    )
    with pytest.raises(SourceParseError, match="invalid_timestamp"):
        parse(request_page, raw, http_status=200)


def test_wrong_entry_namespace_cannot_disguise_nonempty_feed_as_zero(parse, request_page):
    raw = atom(total=0, modify=lambda r: setattr(first(r), "tag", "entry"))
    with pytest.raises(SourceParseError, match="invalid_entry_namespace"):
        parse(request_page, raw, http_status=200)
