from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.research_workflow.application.commands.process_workflow_job import ProcessWorkflowJob
from libs.research_workflow.application.queries.build_crossref_window_plan import (
    BuildCrossrefWindowPlan,
)
from libs.research_workflow.application.queries.build_harvest_query_input import (
    BuildHarvestQueryInput,
)
from libs.research_workflow.domain.services.plan_catchup_jobs import PlanCatchupJobs
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest
from libs.research_workflow.dtos.scheduler import HarvestBindingSchedule, SchedulerSnapshot
from libs.research_workflow.dtos.workflow_job import WorkflowJobCompletion, WorkflowJobLease
from libs.watch_profiles.dtos.domain_definition import DomainDefinition
from libs.watch_profiles.dtos.profile_revision import ProfileRevision


NOW = datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)


def profile() -> ProfileRevision:
    return ProfileRevision(
        "personal",
        3,
        3,
        "a" * 64,
        "active",
        "關注軟體工程、深度學習、機器學習、統計學與羽球研究",
        '{"allow_preprints":true,"exclude":["noise"],"free_only":true,'
        '"include":["causal inference"],"languages":["en"],'
        '"sources":["crossref"]}',
        (("statistics", 7),),
    )


def domain() -> DomainDefinition:
    return DomainDefinition(
        "statistics",
        7,
        "統計學",
        ("statistics", "統計學"),
        ("statistical inference", "statistics"),
        ("noise",),
        ("crossref",),
        (),
    )


def query_input():
    build = BuildHarvestQueryInput(
        Mock(return_value=profile()),
        Mock(return_value=domain()),
    )
    return build(
        HarvestQueryRequest(
            "personal",
            "statistics",
            NOW - timedelta(days=1),
            NOW,
            source_id="crossref",
            deferred_mode="defer",
            time_basis="indexDate",
            page_size=1000,
            expected_profile_revision=3,
            expected_domain_revision=7,
        )
    )


def test_crossref_scheduler_creates_one_bounded_harvest_job() -> None:
    schedule = HarvestBindingSchedule(
        "personal:3:statistics:7:crossref",
        "personal",
        3,
        "statistics",
        7,
        "crossref",
        None,
    )
    plan = PlanCatchupJobs()(
        SchedulerSnapshot((schedule,), (), (), ()),
        now=NOW,
    )

    assert len(plan.jobs) == 1
    job = plan.jobs[0]
    payload = PlanCatchupJobs.decode(job.input_json)
    assert job.job_kind == "harvest_window"
    assert payload["source_id"] == "crossref"
    assert payload["window_start"] == (NOW - timedelta(days=1)).isoformat()
    assert payload["window_end"] == NOW.isoformat()
    assert plan.coverage_gaps == ()


def test_crossref_query_builder_preserves_exact_profile_and_domain_snapshot() -> None:
    query = query_input()

    assert query.source_id == "crossref"
    assert query.profile_id == "personal"
    assert query.profile_revision == 3
    assert query.profile_fingerprint == "a" * 64
    assert query.domain.domain_id == "statistics"
    assert query.domain.revision == 7
    assert query.time_basis == "indexDate"
    assert query.page_size == 1000


def test_crossref_window_scope_is_domain_local_and_contact_is_operational_only() -> None:
    query = query_input()
    first = BuildCrossrefWindowPlan(
        CrossrefSourceAdapter(),
        contact_email="reader@example.com",
    )(query, binding_key="personal:3:statistics:7:crossref")
    second = BuildCrossrefWindowPlan(
        CrossrefSourceAdapter(),
        contact_email="other@example.com",
    )(query, binding_key="personal:3:statistics:7:crossref")

    assert first.definition.scope_query == (
        "causal inference statistical inference statistics 統計學"
    )
    assert "軟體工程" not in first.definition.scope_query
    assert "noise" not in first.definition.scope_query
    assert first.definition.config_version.startswith("p3-d7-")
    assert first.query_fingerprint == second.query_fingerprint
    assert first.parameters_fingerprint != second.parameters_fingerprint


class Clock:
    def __init__(self) -> None:
        self.values = [NOW, NOW + timedelta(seconds=1)]

    def now(self):
        return self.values.pop(0) if self.values else NOW + timedelta(seconds=1)


class Store:
    def __init__(self, lease: WorkflowJobLease) -> None:
        self.lease = lease
        self.completed = []

    def claim_due(self, owner_id, *, now, lease_seconds):
        assert owner_id == "worker:test"
        assert now == NOW
        assert lease_seconds == 60
        value, self.lease = self.lease, None
        return value

    def complete(self, request):
        self.completed.append(request)
        return WorkflowJobCompletion(
            request.job_id,
            request.state,
            request.fencing_token,
            False,
        )


def test_crossref_job_without_runtime_defers_and_never_falls_back_to_arxiv() -> None:
    payload = (
        '{"binding_key":"personal:3:statistics:7:crossref",'
        '"domain_id":"statistics","domain_revision":7,'
        '"profile_id":"personal","profile_revision":3,"source_id":"crossref",'
        '"window_end":"2026-09-25T00:00:00+00:00",'
        '"window_start":"2026-09-24T00:00:00+00:00"}'
    )
    lease = WorkflowJobLease(
        "job:" + "a" * 64,
        "harvest_window",
        "business:test",
        payload,
        "b" * 64,
        "worker:test",
        3,
        "job-attempt:" + "c" * 64,
        1,
        NOW + timedelta(seconds=60),
    )
    store = Store(lease)
    arxiv = Mock()
    builder = Mock(return_value=object())
    command = ProcessWorkflowJob(
        store=store,
        builder=builder,
        harvest=arxiv,
        clock=Clock(),
        live_source_enabled=True,
        crossref=None,
        crossref_plan=None,
    )

    result = command("worker:test", lease_seconds=60)

    request = builder.call_args.args[0]
    assert request.source_id == "crossref"
    assert request.time_basis == "indexDate"
    assert request.page_size == 1000
    assert result.state == "awaiting_external"
    assert store.completed[0].error_code == "crossref_runtime_not_connected"
    arxiv.assert_not_called()
