from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.research_workflow.application.queries.build_harvest_query_input import BuildHarvestQueryInput
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.watch_profiles.dtos.domain_definition import DomainDefinition
from libs.watch_profiles.dtos.profile_revision import ProfileRevision

START = datetime(2026, 9, 22, tzinfo=timezone.utc)
END = datetime(2026, 9, 23, tzinfo=timezone.utc)


def profile(**changes):
    base = ProfileRevision(
        "personal",
        3,
        3,
        "a" * 64,
        "active",
        "只看研究",
        '{"allow_preprints":true,"exclude":["noise"],"free_only":false,'
        '"include":["causal"],"languages":[],"sources":["arxiv","crossref"]}',
        (("statistics", 7),),
    )
    return replace(base, **changes)


def domain(**changes):
    base = DomainDefinition(
        "statistics",
        7,
        "統計學",
        ("statistics", "統計學"),
        ("statistics",),
        (),
        ("arxiv", "crossref"),
        (("arxiv", ("stat.AP", "stat.CO", "stat.ME", "stat.ML", "stat.OT", "stat.TH")),),
    )
    return replace(base, **changes)


def request(**changes):
    base = HarvestQueryRequest(
        "personal",
        "statistics",
        START,
        END,
        deferred_mode="defer",
        page_size=50,
    )
    return replace(base, **changes)


def builder(profile_value=None, domain_value=None):
    profiles = Mock(return_value=profile_value or profile())
    domains = Mock(return_value=domain_value or domain())
    return BuildHarvestQueryInput(profiles, domains), profiles, domains


def test_exact_published_profile_and_domain_build_reproducible_query_input():
    build, profiles, domains = builder()
    query = build(request())
    assert query.profile_id == "personal" and query.profile_revision == 3
    assert query.domain.domain_id == "statistics" and query.domain.revision == 7
    assert query.domain.categories == (
        "stat.AP",
        "stat.CO",
        "stat.ME",
        "stat.ML",
        "stat.OT",
        "stat.TH",
    )
    assert query.profile_include == ("causal",)
    assert query.profile_exclude == ("noise",)
    assert query.page_size == 50
    plan = ArxivQueryCompilerAdapter().compile(query)
    assert "cat:stat.AP" in plan.search_query and "cat:stat.TH" in plan.search_query
    profiles.assert_called_once_with("personal")
    domains.assert_called_once_with("statistics", 7)


@pytest.mark.parametrize(
    "profile_value,code",
    [
        (profile(lifecycle="paused"), "profile_not_current"),
        (profile(current_revision=4), "profile_not_current"),
        (profile(domains=()), "domain_not_selected"),
        (profile(domains=(("statistics", True),)), "invalid_profile_snapshot"),
        (
            profile(
                filters_json='{"sources":["arxiv"],"sources":["crossref"],'
                '"include":[],"exclude":[],"languages":[],"free_only":false,'
                '"allow_preprints":true}'
            ),
            "invalid_profile_snapshot",
        ),
    ],
)
def test_invalid_profile_state_fails_before_domain_or_source_work(profile_value, code):
    build, _, domains = builder(profile_value=profile_value)
    with pytest.raises(HarvestWorkflowError, match=code):
        build(request())
    if code != "invalid_profile_snapshot" or profile_value.domains != (("statistics", True),):
        assert domains.call_count == 0


def test_exact_domain_revision_mismatch_is_rejected():
    build, _, _ = builder(domain_value=domain(revision=8))
    with pytest.raises(HarvestWorkflowError, match="invalid_domain_snapshot"):
        build(request())


def test_source_must_be_selected_by_both_profile_and_domain():
    build, _, _ = builder(domain_value=domain(sources=("crossref",), source_categories=()))
    with pytest.raises(HarvestWorkflowError, match="source_not_selected"):
        build(request())


def test_non_arxiv_source_is_outside_this_task():
    build, profiles, domains = builder()
    with pytest.raises(HarvestWorkflowError, match="unsupported_source"):
        build(request(source_id="crossref"))
    profiles.assert_not_called()
    domains.assert_not_called()


def test_scheduled_job_must_not_silently_switch_to_a_newer_profile_revision():
    build, _, domains = builder(
        profile_value=profile(revision=4, current_revision=4, domains=(("statistics", 7),))
    )
    with pytest.raises(HarvestWorkflowError, match="scheduled_input_stale"):
        build(request(expected_profile_revision=3, expected_domain_revision=7))
    domains.assert_not_called()


def test_scheduled_job_must_not_silently_switch_domain_revision():
    build, _, _ = builder()
    with pytest.raises(HarvestWorkflowError, match="scheduled_input_stale"):
        build(request(expected_profile_revision=3, expected_domain_revision=6))
