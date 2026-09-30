"""Author-owned offline protocol oracles; not the frozen T20 acceptance suite."""

import hashlib
import json
import socket
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError

START = datetime(2026, 9, 20, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def definition(**changes):
    return replace(
        CrossrefWindowInput(
            binding_key="personal:3:statistics:1:crossref",
            scope_query="statistical learning",
            from_index=START,
            until_index=END,
            contact_email="fixture@example.invalid",
            config_version="profile-query-v1",
            rows=2,
        ),
        **changes,
    )


def setup(**changes):
    source = CrossrefSourceAdapter()
    plan = source.compile(definition(**changes))
    return source, plan, source.page(plan)


def response(items=(), *, total=123, cursor="opaque+next/%==", **fields):
    message = {"items": list(items), "total-results": total, **fields}
    if cursor is not None:
        message["next-cursor"] = cursor
    return json.dumps(
        {"status": "ok", "message-type": "work-list", "message": message},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def decode(source, plan, request, body, **kwargs):
    return source.decode(
        plan, request, body, expected_sha256=hashlib.sha256(body).hexdigest(), **kwargs
    )


def item(doi="10.1234/ABC", **metadata):
    return {"DOI": doi, **metadata}


def test_scoped_bounded_query_preserves_inclusive_boundary():
    source, plan, request = setup()
    params = parse_qs(urlsplit(request.url).query)
    assert urlsplit(request.url).netloc == "api.crossref.org"
    assert urlsplit(request.url).path == "/works"
    assert params == {
        "filter": ["from-index-date:2026-09-20T00:00:00,until-index-date:2026-09-21T00:00:00"],
        "query.bibliographic": ["statistical learning"],
        "rows": ["2"],
        "mailto": ["fixture@example.invalid"],
        "cursor": ["*"],
    }
    adjacent = source.compile(definition(from_index=END, until_index=END + timedelta(days=1)))
    following = parse_qs(urlsplit(source.page(adjacent).url).query)
    assert "from-index-date:2026-09-21T00:00:00" in following["filter"][0]
    assert plan.query_fingerprint != adjacent.query_fingerprint


def test_timezone_equivalent_windows_have_same_identity():
    source, plan, _ = setup()
    tz = timezone(timedelta(hours=8))
    equivalent = source.compile(definition(from_index=START.astimezone(tz), until_index=END.astimezone(tz)))
    assert plan.query_fingerprint == equivalent.query_fingerprint
    assert plan.parameters == equivalent.parameters


@pytest.mark.parametrize("change", [
    {"scope_query": "badminton"}, {"binding_key": "another-binding"},
    {"config_version": "profile-query-v2"}, {"rows": 1},
])
def test_semantic_changes_get_new_query_identity(change):
    source, plan, _ = setup()
    assert source.compile(definition(**change)).query_fingerprint != plan.query_fingerprint


def test_contact_changes_parameters_not_semantic_query_identity():
    source, plan, _ = setup()
    alternate = source.compile(definition(contact_email="other@example.invalid"))
    assert alternate.query_fingerprint == plan.query_fingerprint
    assert alternate.parameters_fingerprint != plan.parameters_fingerprint


@pytest.mark.parametrize("cursor", ["a+b/c%==", "%2Fnot-yet-decoded", "opaque&x=1#fragment", "游標+甲"])
def test_opaque_cursor_encoded_once_and_full_parameters_repeated(cursor):
    source, plan, original = setup()
    request = source.page(plan, cursor)
    first = parse_qs(urlsplit(original.url).query)
    second = parse_qs(urlsplit(request.url).query)
    assert second.pop("cursor") == [cursor]
    first.pop("cursor")
    assert first == second
    assert source.page(plan, cursor) == request
    assert request.request_fingerprint != original.request_fingerprint


@pytest.mark.parametrize("change", [
    {"rows": 0}, {"rows": 1001}, {"rows": True}, {"rows": 1.0},
    {"scope_query": ""}, {"scope_query": "   "}, {"scope_query": "x\n&filter=y"},
    {"binding_key": ""}, {"config_version": ""}, {"contact_email": "not-email"},
    {"contact_email": "a@example.invalid\nBcc:other"},
    {"from_index": START.replace(tzinfo=None)}, {"until_index": START},
    {"until_index": END + timedelta(seconds=1)},
    {"until_index": END.replace(microsecond=1)},
    {"from_index": "2026-09-20"},
])
def test_invalid_window_configuration_is_rejected(change):
    with pytest.raises(CrossrefProtocolError):
        CrossrefSourceAdapter().compile(definition(**change))


def test_rows_maximum_is_allowed():
    source, plan, _ = setup(rows=1000)
    assert parse_qs(urlsplit(source.page(plan).url).query)["rows"] == ["1000"]


@pytest.mark.parametrize("cursor", ["", " ", "abc\n", " abc", 123, None, "x" * 65537])
def test_invalid_cursor_is_rejected(cursor):
    source, plan, _ = setup()
    with pytest.raises(CrossrefProtocolError):
        source.page(plan, cursor)


@pytest.mark.parametrize("change", [
    {"query_fingerprint": "a" * 64}, {"parameters_fingerprint": "b" * 64},
    {"parameters": (("rows", "999"),)},
])
def test_tampered_plan_rejected(change):
    source, plan, _ = setup()
    with pytest.raises(CrossrefProtocolError, match="invalid_crossref_plan"):
        source.page(replace(plan, **change))


@pytest.mark.parametrize("change", [
    {"url": "https://evil.invalid/works"}, {"cursor": "other"},
    {"query_fingerprint": "a" * 64}, {"parameters_fingerprint": "b" * 64},
    {"request_fingerprint": "c" * 64},
])
def test_tampered_request_rejected_before_body_decode(change):
    source, plan, request = setup()
    with pytest.raises(CrossrefProtocolError, match="invalid_crossref_request"):
        decode(source, plan, replace(request, **change), b"not-json")


def test_duplicate_dois_and_changed_bodies_keep_all_positions():
    source, plan, request = setup(rows=3)
    body = response([item(), item(), item(title=["changed"])] )
    parsed = decode(source, plan, request, body)
    assert [value.ordinal for value in parsed.items] == [0, 1, 2]
    assert [value.state for value in parsed.items] == ["decoded"] * 3
    assert [value.doi_raw for value in parsed.items] == ["10.1234/ABC"] * 3
    assert parsed.items[0].canonical_sha256 == parsed.items[1].canonical_sha256
    assert parsed.items[1].canonical_sha256 != parsed.items[2].canonical_sha256
    assert parsed.traversal_end_hint is False


def test_unknown_metadata_and_jats_remain_unexecuted_json_strings():
    source, plan, request = setup()
    record = item(
        abstract='<jats:p><script>not executable</script>&amp;</jats:p>',
        relation={"future-predicate": [{"id-type": "mystery", "id": "target"}]},
        **{"update-to": [{"type": "retraction", "DOI": "10.1234/target"}], "vendor-field": [1, 2]},
    )
    parsed = decode(source, plan, request, response([record]))
    assert json.loads(parsed.items[0].canonical_json) == record
    assert parsed.items[0].doi_raw == record["DOI"]
    assert parsed.items[0].state == "decoded"
    assert not hasattr(parsed.items[0], "work_id")
    assert not hasattr(parsed, "checkpoint")


@pytest.mark.parametrize("bad", [None, [], "text", 3, {}, {"DOI": None}, {"DOI": 12}, {"DOI": " "}])
def test_bad_item_quarantined_without_discarding_valid_sibling(bad):
    source, plan, request = setup(rows=3)
    parsed = decode(source, plan, request, response([bad, item()]))
    assert [value.state for value in parsed.items] == ["quarantined", "decoded"]
    assert parsed.items[0].error_code is not None
    assert json.loads(parsed.items[0].canonical_json) == bad
    assert parsed.items[1].ordinal == 1
    assert parsed.traversal_end_hint is True


@pytest.mark.parametrize("total", [0, 1, 99999, None])
def test_total_is_advisory_and_never_stops_full_page(total):
    source, plan, request = setup()
    parsed = decode(source, plan, request, response([item(), item()], total=total))
    assert parsed.reported_total == total
    assert parsed.traversal_end_hint is False


@pytest.mark.parametrize("cursor", [None, "opaque", "*"])
def test_short_page_termination_does_not_require_total_equality(cursor):
    source, plan, request = setup()
    parsed = decode(source, plan, request, response([item()], total=9999, cursor=cursor))
    assert parsed.traversal_end_hint is True
    assert parsed.next_cursor == cursor


def test_empty_page_termination_retains_unknown_completeness():
    source, plan, request = setup()
    parsed = decode(source, plan, request, response([], total=99, cursor=None))
    assert parsed.traversal_end_hint is True
    assert parsed.items == ()
    assert not hasattr(parsed, "complete")


@pytest.mark.parametrize("cursor", [None, "", "*", 5])
def test_full_page_without_progress_cursor_is_not_complete(cursor):
    source, plan, request = setup()
    with pytest.raises(CrossrefProtocolError):
        decode(source, plan, request, response([item(), item()], cursor=cursor))


def test_immediate_cursor_cycle_rejected():
    source, plan, _ = setup()
    request = source.page(plan, "same")
    with pytest.raises(CrossrefProtocolError, match="crossref_cursor_no_progress"):
        decode(source, plan, request, response([item(), item()], cursor="same"))


@pytest.mark.parametrize("status", [301, 403, 429, 500, True])
def test_non_success_http_status_never_decodes_metadata(status):
    source, plan, request = setup()
    with pytest.raises(CrossrefProtocolError):
        decode(source, plan, request, response([item()]), http_status=status)


@pytest.mark.parametrize("body", [
    b"not json", b"\xff", b"[]", b'{"status":"ok","status":"ok"}',
    b'{"status":"ok","message-type":"work-list","message":{"items":[],"x":NaN}}',
    b'{"status":"ok","message-type":"work-list","message":{"items":[],"x":1e999}}',
    b'{"status":"ok","message-type":"work","message":{"items":[]}}',
    b'{"status":"error","message-type":"work-list","message":{"items":[]}}',
    b'{"status":"ok","message-type":"work-list","message":[]}',
    b'{"status":"ok","message-type":"work-list","message":{"items":null}}',
    b'{"status":"ok","message-type":"work-list","message":{"items":[],"total-results":true}}',
    b'{"status":"ok","message-type":"work-list","message":{"items":[],"total-results":-1}}',
    b'{"status":"ok","message-type":"work-list","message":{"items":[{"DOI":"a","DOI":"b"}]}}',
    b'{"status":"ok","message-type":"work-list","message":{"items":[' + b"[" * 65 + b"0" + b"]" * 65 + b"]}}",
])
def test_invalid_envelope_rejected_without_exposing_body(body):
    source, plan, request = setup()
    with pytest.raises(CrossrefProtocolError) as error:
        decode(source, plan, request, body)
    assert str(error.value) == error.value.code
    assert error.value.response_sha256 == hashlib.sha256(body).hexdigest()


def test_too_many_items_rejected():
    source, plan, request = setup(rows=1)
    with pytest.raises(CrossrefProtocolError, match="invalid_crossref_envelope"):
        decode(source, plan, request, response([item(), item()]))


def test_oversized_raw_body_rejected():
    source, plan, request = setup()
    with pytest.raises(CrossrefProtocolError, match="crossref_response_too_large"):
        decode(source, plan, request, b" " * 8_000_001)


def test_expected_raw_hash_is_checked():
    source, plan, request = setup()
    body = response([item()])
    with pytest.raises(CrossrefProtocolError, match="crossref_response_hash_mismatch"):
        source.decode(plan, request, body, expected_sha256="0" * 64)


def test_escape_heavy_metadata_not_mistaken_for_deep_json():
    source, plan, request = setup()
    value = '[[[[\\"' * 100
    parsed = decode(source, plan, request, response([item(abstract=value)]))
    assert json.loads(parsed.items[0].canonical_json)["abstract"] == value


def test_local_raw_replay_deterministic_but_refetched_page_may_change():
    source, plan, request = setup()
    first_body = response([item()], total=3)
    before = decode(source, plan, request, first_body)
    assert decode(source, plan, request, first_body) == before
    after = decode(source, plan, request, response([item(title=["new"])], total=5))
    assert after.response_sha256 != before.response_sha256
    assert after.reported_total != before.reported_total


def test_compile_page_decode_perform_no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("protocol leaf must not open a network socket")
    monkeypatch.setattr(socket, "socket", forbidden)
    source, plan, request = setup()
    assert decode(source, plan, request, response([item()])).items[0].state == "decoded"
