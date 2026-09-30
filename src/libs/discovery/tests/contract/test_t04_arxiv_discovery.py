import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.application.queries.compile_source_query import CompileSourceQuery
from libs.discovery.domain.services.evaluate_source_page import EvaluateSourcePage
from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.source_page_observation import SourcePageObservation
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.source_query_error import SourceQueryError


@pytest.fixture
def source():
    return ArxivQueryCompilerAdapter()


@pytest.fixture
def query():
    return SourceQueryInput(
        source_id="arxiv",
        profile_id="personal",
        profile_revision=1,
        profile_fingerprint="a" * 64,
        domain=DomainQuerySnapshot(
            domain_id="machine_learning",
            revision=1,
            sources=("arxiv",),
            categories=("cs.LG", "stat.ML"),
            aliases=("機器學習",),
            include=("machine learning",),
        ),
        window_start=datetime(2026, 9, 22, tzinfo=timezone.utc),
        window_end=datetime(2026, 9, 23, tzinfo=timezone.utc),
        profile_sources=("arxiv",),
    )


def observation(request, *, total=3, ids=("fixture-a-v1", "fixture-b-v1"), **kwargs):
    return SourcePageObservation(
        source_id="arxiv",
        query_fingerprint=request.query_fingerprint,
        start_index=request.start,
        total_results=total,
        record_ids=ids,
        **kwargs,
    )


def test_capabilities_are_descriptive_not_live_verification(source):
    cap = source.describe()
    assert cap.source_id == "arxiv"
    assert cap.minimum_request_interval_seconds == 3
    assert cap.maximum_connections == 1
    assert cap.maximum_page_size == 2000
    assert "languages" in cap.unsupported_native_filters
    assert cap.supported_time_bases == ("submittedDate",)
    assert not cap.snapshot_pagination
    assert not cap.live_verified


def test_exact_query_compilation_through_inbound_use_case(source, query):
    plan = CompileSourceQuery(source)(query)
    assert plan.search_query == (
        '((cat:cs.LG OR cat:stat.ML) OR (ti:"machine learning" OR abs:"machine learning" '
        'OR ti:"機器學習" OR abs:"機器學習")) '
        "AND submittedDate:[202609220000 TO 202609230000]"
    )
    assert not plan.deferred_filters
    assert "submission_window_is_not_revision_feed" in plan.warnings
    provenance = json.loads(plan.provenance_json)
    assert provenance["input"]["profile_fingerprint"] == "a" * 64
    assert provenance["input"]["domain"]["revision"] == 1
    assert provenance["policy"] == "category-or-terms-v1"
    assert provenance["window_semantics"] == "inclusive-minutes-utc"
    assert len(plan.query_fingerprint) == 64
    with pytest.raises(FrozenInstanceError):
        plan.search_query = "all:*"


def test_reordered_sets_produce_identical_request_identity(source, query):
    domain = replace(query.domain, categories=("stat.ML", "cs.LG", "cs.LG"))
    left = source.compile(query)
    right = source.compile(replace(query, domain=domain))
    assert left == right
    assert source.page(left) == source.page(right)


def test_timezone_equivalent_windows_are_identical(source, query):
    taipei = timezone(timedelta(hours=8))
    other = replace(
        query,
        window_start=query.window_start.astimezone(taipei),
        window_end=query.window_end.astimezone(taipei),
    )
    assert source.compile(query) == source.compile(other)


def test_filters_are_scoped_and_precedence_is_explicit(source, query):
    domain = replace(query.domain, exclude=("generic sport",))
    plan = source.compile(
        replace(query, domain=domain, profile_include=("program repair", "C++"), profile_exclude=("survey",))
    )
    assert 'AND (ti:"C++" OR abs:"C++" OR ti:"program repair" OR abs:"program repair")' in plan.search_query
    assert (
        'ANDNOT (ti:"generic sport" OR abs:"generic sport" OR ti:"survey" OR abs:"survey")'
        in plan.search_query
    )
    request = source.page(plan)
    split = urlsplit(request.url)
    assert (split.scheme, split.netloc, split.path) == ("https", "export.arxiv.org", "/api/query")
    params = parse_qs(split.query, strict_parsing=True)
    assert params["search_query"] == [plan.search_query]
    assert params["sortBy"] == ["submittedDate"]
    assert params["sortOrder"] == ["ascending"]
    assert params["start"] == ["0"]
    assert params["max_results"] == ["200"]


@pytest.mark.parametrize(
    "change",
    [
        {"languages": ("en",)},
        {"free_only": True},
        {"allow_preprints": False},
        {"scope_text": "我只想看可以重現的方法"},
    ],
)
def test_unsupported_filters_rejected_unless_explicitly_deferred(source, query, change):
    with pytest.raises(SourceQueryError, match="unsupported_filters"):
        source.compile(replace(query, **change))
    plan = source.compile(replace(query, deferred_mode="defer", **change))
    assert len(plan.deferred_filters) == 1
    decision = plan.deferred_filters[0]
    assert decision.name == next(iter(change))
    assert decision.handling == "unsupported_at_source"
    assert decision.required_stage
    assert json.loads(decision.value_json) == json.loads(json.dumps(next(iter(change.values()))))


def test_update_sort_is_not_a_supported_update_window(source, query):
    with pytest.raises(SourceQueryError, match="unsupported_time_basis"):
        source.compile(replace(query, time_basis="lastUpdatedDate"))


@pytest.mark.parametrize(
    "change,code",
    [
        ({"source_id": "pubmed"}, "unsupported_source"),
        ({"profile_sources": ("pubmed",)}, "source_not_selected"),
        ({"profile_revision": True}, "invalid_revision"),
        ({"profile_revision": 0}, "invalid_revision"),
        ({"profile_revision": 2**63}, "invalid_revision"),
        ({"profile_fingerprint": "not-a-hash"}, "invalid_fingerprint"),
        ({"page_size": True}, "invalid_page_size"),
        ({"page_size": 0}, "invalid_page_size"),
        ({"page_size": 2001}, "invalid_page_size"),
        ({"deferred_mode": "ignore"}, "invalid_deferred_mode"),
        ({"free_only": "false"}, "invalid_boolean"),
        ({"allow_preprints": 1}, "invalid_boolean"),
        ({"profile_include": ["list-not-tuple"]}, "invalid_terms"),
        ({"window_start": datetime(2026, 9, 22)}, "timezone_required"),
        ({"window_start": datetime(2026, 9, 24, tzinfo=timezone.utc)}, "invalid_window"),
        ({"window_end": datetime(2026, 9, 23, 0, 0, 1, tzinfo=timezone.utc)}, "unsupported_time_precision"),
    ],
)
def test_invalid_input_fails_with_stable_code(source, query, change, code):
    with pytest.raises(SourceQueryError) as result:
        source.compile(replace(query, **change))
    assert result.value.code == code


@pytest.mark.parametrize("phrase", ['x" OR all:*', r"x\y", "x\x00y", "x\ny", "\ud800", "x" * 513])
def test_unsafe_or_unrepresentable_phrase_is_not_silently_rewritten(source, query, phrase):
    with pytest.raises(SourceQueryError):
        source.compile(replace(query, profile_include=(phrase,)))


@pytest.mark.parametrize("category", ["stat.*", "cs.LG OR all:*", "cs.LG&start=9000", "", "cs.LG\n"])
def test_category_is_not_raw_query_syntax(source, query, category):
    with pytest.raises(SourceQueryError, match="invalid_category"):
        source.compile(replace(query, domain=replace(query.domain, categories=(category,))))


def test_empty_scope_and_disabled_source_do_not_expand_to_everything(source, query):
    empty = replace(query.domain, categories=(), aliases=(), include=())
    with pytest.raises(SourceQueryError, match="empty_discovery_scope"):
        source.compile(replace(query, domain=empty))
    with pytest.raises(SourceQueryError, match="source_not_selected"):
        source.compile(replace(query, domain=replace(query.domain, sources=("pubmed",))))


@pytest.mark.parametrize(
    "change",
    [
        {"profile_revision": 2},
        {"profile_fingerprint": "b" * 64},
        {"profile_id": "other"},
        {"window_end": datetime(2026, 9, 24, tzinfo=timezone.utc)},
        {"page_size": 100},
    ],
)
def test_reproducibility_identity_changes_when_scope_or_execution_plan_changes(source, query, change):
    assert (
        source.compile(query).query_fingerprint != source.compile(replace(query, **change)).query_fingerprint
    )


def test_domain_revision_and_definition_are_part_of_identity(source, query):
    original = source.compile(query)
    for changed in (replace(query.domain, revision=2), replace(query.domain, include=("new method",))):
        assert source.compile(replace(query, domain=changed)).query_fingerprint != original.query_fingerprint


def test_page_offsets_have_distinct_request_identity_not_distinct_query(source, query):
    plan = source.compile(query)
    first, second = source.page(plan, 0), source.page(plan, 200)
    assert first.query_fingerprint == second.query_fingerprint
    assert first.request_fingerprint != second.request_fingerprint
    assert parse_qs(urlsplit(second.url).query)["start"] == ["200"]
    assert source.page(plan, 29999).max_results == 1


@pytest.mark.parametrize("offset", [-1, True, "0", 30000])
def test_invalid_offset_cannot_create_request(source, query, offset):
    with pytest.raises(SourceQueryError, match="invalid_offset"):
        source.page(source.compile(query), offset)


def test_accidental_plan_tampering_is_detected(source, query):
    plan = source.compile(query)
    for changed in (
        replace(plan, search_query="all:*"),
        replace(plan, page_size=2000),
        replace(plan, query_fingerprint="f" * 64),
    ):
        with pytest.raises(SourceQueryError, match="invalid_compiled_plan"):
            source.page(changed)


def test_synthetic_pages_replay_same_time_identities_without_claiming_durable_checkpoint(source, query):
    path = Path(__file__).parents[1] / "fixtures/arxiv_pages.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    assert fixture["pages"][0]["submitted"][1] == fixture["pages"][1]["submitted"][0]
    plan = source.compile(replace(query, page_size=fixture["page_size"]))
    evaluate = EvaluateSourcePage()
    identities = set()
    for _ in range(2):
        offset = 0
        for page in fixture["pages"]:
            request = source.page(plan, offset)
            observed = observation(request, total=page["total"], ids=tuple(page["ids"]))
            decision = evaluate(request, observed, expected_total_results=5)
            identities.update(observed.record_ids)
            offset = decision.next_offset
        assert offset is None
    assert identities == {f"fixture-{letter}-v1" for letter in "abcde"}
    assert decision.is_last and not decision.verified_empty


def test_only_successful_valid_first_zero_page_is_verified_empty(source, query):
    request = source.page(source.compile(replace(query, page_size=2)))
    decision = EvaluateSourcePage()(request, observation(request, total=0, ids=()))
    assert decision.verified_empty and decision.is_last and decision.next_offset is None


@pytest.mark.parametrize(
    "change,code",
    [
        ({"status": "http_error"}, "source_page_failed"),
        ({"status": "parse_error"}, "source_page_failed"),
        ({"start_index": 1}, "page_offset_mismatch"),
        ({"query_fingerprint": "b" * 64}, "page_query_mismatch"),
        ({"source_id": "pubmed"}, "page_query_mismatch"),
        ({"total_results": True}, "invalid_page_counts"),
        ({"total_results": -1}, "invalid_page_counts"),
        ({"total_results": 30001}, "query_window_too_large"),
        ({"record_ids": ()}, "unexpected_empty_page"),
        ({"record_ids": ("fixture-a-v1",)}, "incomplete_page"),
        ({"record_ids": ("x", "x")}, "duplicate_page_identity"),
        ({"record_ids": ("x", "y", "z")}, "invalid_page_counts"),
        ({"record_ids": ("", "y")}, "invalid_page_identity"),
    ],
)
def test_invalid_pages_do_not_produce_success_or_next_page(source, query, change, code):
    request = source.page(source.compile(replace(query, page_size=2)))
    with pytest.raises(SourceQueryError) as result:
        EvaluateSourcePage()(request, replace(observation(request), **change))
    assert result.value.code == code


def test_changed_total_requires_reconciliation_not_silent_continuation(source, query):
    request = source.page(source.compile(replace(query, page_size=2)))
    with pytest.raises(SourceQueryError, match="source_result_set_changed"):
        EvaluateSourcePage()(request, observation(request), expected_total_results=4)


def test_unchanged_total_is_not_a_snapshot_guarantee(source, query):
    plan = source.compile(query)
    assert "offset_pagination_not_snapshot" in plan.warnings
    assert not source.describe().snapshot_pagination


def test_timestamp_format_is_platform_independent_for_four_digit_year(source, query):
    ancient = replace(
        query,
        window_start=datetime(999, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(999, 1, 2, tzinfo=timezone.utc),
    )
    assert "submittedDate:[099901010000 TO 099901020000]" in source.compile(ancient).search_query


def test_malformed_defer_mode_has_stable_error_not_type_error(source, query):
    with pytest.raises(SourceQueryError, match="invalid_deferred_mode"):
        source.compile(replace(query, deferred_mode=[]))


def test_special_characters_cannot_add_url_parameters(source, query):
    plan = source.compile(replace(query, profile_include=("C++ &start=9000 # test",)))
    parsed = urlsplit(source.page(plan).url)
    params = parse_qs(parsed.query, strict_parsing=True)
    assert not parsed.fragment
    assert set(params) == {"search_query", "start", "max_results", "sortBy", "sortOrder"}
    assert params["start"] == ["0"]
    assert params["search_query"] == [plan.search_query]


def test_excessively_large_query_is_explicitly_rejected(source, query):
    words = tuple(f"term{n}" + "a" * 250 for n in range(100))
    with pytest.raises(SourceQueryError, match="query_too_large"):
        source.compile(replace(query, profile_include=words))


@pytest.mark.parametrize("ids", [["a", "b"], (1, "b"), (None, "b")])
def test_bad_page_identity_types_cannot_create_traversal_decision(source, query, ids):
    request = source.page(source.compile(replace(query, page_size=2)))
    with pytest.raises(SourceQueryError, match="invalid_page_identity"):
        EvaluateSourcePage()(request, observation(request, ids=ids))
