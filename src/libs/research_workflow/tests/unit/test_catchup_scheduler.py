import pytest

from datetime import datetime, timedelta, timezone

from libs.research_workflow.application.commands.run_scheduler_tick import RunSchedulerTick
from libs.research_workflow.domain.services.plan_catchup_jobs import PlanCatchupJobs
from libs.research_workflow.dtos.scheduler import (
    CoverageGap,
    DeliverySchedule,
    HarvestBindingSchedule,
    KnownWorkflowJob,
    SchedulerSnapshot,
)
from libs.research_workflow.dtos.workflow_job import EnqueuedWorkflowJob
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


def binding(
    key: str,
    *,
    source: str = "arxiv",
    last: datetime | None = None,
) -> HarvestBindingSchedule:
    return HarvestBindingSchedule(
        binding_key=key,
        profile_id="personal",
        profile_revision=3,
        domain_id=key,
        domain_revision=1,
        source_id=source,
        last_succeeded_window_end=last,
    )


def delivery(last: datetime | None = None) -> DeliverySchedule:
    return DeliverySchedule(
        subscription_id="subscription:daily",
        timezone="Asia/Taipei",
        local_time="08:00",
        last_scheduled_cutoff=last,
    )


def test_each_supported_binding_advances_only_one_24h_window_per_tick() -> None:
    snapshot = SchedulerSnapshot(
        harvest_bindings=(
            binding("statistics", last=NOW - timedelta(hours=48)),
            binding("machine_learning", last=NOW - timedelta(hours=24)),
        ),
        delivery_schedules=(),
        known_jobs=(),
        input_gaps=(),
    )

    plan = PlanCatchupJobs()(snapshot, now=NOW)

    assert len(plan.jobs) == 2
    first, second = plan.jobs
    assert first.job_kind == second.job_kind == "harvest_window"
    payloads = [PlanCatchupJobs.decode(job.input_json) for job in plan.jobs]
    assert {
        (payload["binding_key"], payload["window_start"], payload["window_end"])
        for payload in payloads
    } == {
        (
            "machine_learning",
            (NOW - timedelta(hours=24)).isoformat(),
            NOW.isoformat(),
        ),
        (
            "statistics",
            (NOW - timedelta(hours=48)).isoformat(),
            (NOW - timedelta(hours=24)).isoformat(),
        ),
    }


def test_failed_window_is_left_for_job_store_retry_and_never_rebuilt() -> None:
    schedule = binding("statistics", last=NOW - timedelta(hours=48))
    expected = PlanCatchupJobs.harvest_job(schedule, NOW - timedelta(hours=24))
    snapshot = SchedulerSnapshot(
        harvest_bindings=(schedule,),
        delivery_schedules=(),
        known_jobs=(KnownWorkflowJob(expected.business_key, "failed", "statistics"),),
        input_gaps=(),
    )

    plan = PlanCatchupJobs()(snapshot, now=NOW)

    assert plan.jobs == ()
    assert any(
        gap.identity == "statistics" and gap.reason == "harvest_window_failed"
        for gap in plan.coverage_gaps
    )


def test_unsupported_source_is_a_coverage_gap_not_a_fake_success_job() -> None:
    snapshot = SchedulerSnapshot(
        harvest_bindings=(
            binding("badminton:crossref", source="crossref"),
        ),
        delivery_schedules=(),
        known_jobs=(),
        input_gaps=(),
    )

    plan = PlanCatchupJobs()(snapshot, now=NOW)

    assert plan.jobs == ()
    assert {(gap.identity, gap.reason) for gap in plan.coverage_gaps} == {
        ("badminton:crossref", "source_scheduler_not_supported"),
    }


def test_daily_digest_catchup_collapses_missed_days_into_one_latest_period() -> None:
    last = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)
    snapshot = SchedulerSnapshot(
        harvest_bindings=(),
        delivery_schedules=(delivery(last),),
        known_jobs=(),
        input_gaps=(),
    )

    plan = PlanCatchupJobs()(snapshot, now=NOW)

    assert len(plan.jobs) == 1
    job = plan.jobs[0]
    assert job.job_kind == "prepare_digest"
    payload = PlanCatchupJobs.decode(job.input_json)
    # Asia/Taipei 08:00 is 00:00 UTC. We create only the latest 2026-09-24 period,
    # with a period_start that reaches back to the previously scheduled cutoff.
    assert payload["period_key"] == "2026-09-24"
    assert payload["period_start"] == last.isoformat()
    assert payload["cutoff_at"] == NOW.isoformat()


def test_new_or_pending_harvest_work_defers_digest_until_a_later_tick() -> None:
    schedule = binding("statistics", last=NOW - timedelta(hours=24))
    harvest = PlanCatchupJobs.harvest_job(schedule, NOW)
    for known in (
        (),
        (KnownWorkflowJob(harvest.business_key, "pending", "statistics"),),
        (KnownWorkflowJob(harvest.business_key, "running", "statistics"),),
    ):
        snapshot = SchedulerSnapshot(
            harvest_bindings=(schedule,),
            delivery_schedules=(delivery(NOW - timedelta(days=1)),),
            known_jobs=known,
            input_gaps=(),
        )

        plan = PlanCatchupJobs()(snapshot, now=NOW)

        expected_jobs = ["harvest_window"] if not known else []
        assert [job.job_kind for job in plan.jobs] == expected_jobs
        assert plan.digest_deferred is True


def test_failed_harvest_does_not_disappear_but_digest_may_continue_with_gap() -> None:
    schedule = binding("statistics", last=NOW - timedelta(hours=24))
    harvest = PlanCatchupJobs.harvest_job(schedule, NOW)
    snapshot = SchedulerSnapshot(
        harvest_bindings=(schedule,),
        delivery_schedules=(delivery(NOW - timedelta(days=1)),),
        known_jobs=(KnownWorkflowJob(harvest.business_key, "failed", "statistics"),),
        input_gaps=(),
    )

    plan = PlanCatchupJobs()(snapshot, now=NOW)

    assert [job.job_kind for job in plan.jobs] == ["prepare_digest"]
    digest_payload = PlanCatchupJobs.decode(plan.jobs[0].input_json)
    assert digest_payload["coverage_gaps"] == [
        {
            "identity": "statistics",
            "kind": "harvest",
            "reason": "harvest_window_failed",
        }
    ]


class FakeStore:
    def __init__(self, replay: set[str]) -> None:
        self.replay = replay
        self.calls = []

    def enqueue(self, request):
        self.calls.append(request.business_key)
        return EnqueuedWorkflowJob(
            "job:" + "a" * 64,
            request.business_key in self.replay,
        )


class FakeInputs:
    def __init__(self, snapshot: SchedulerSnapshot) -> None:
        self.snapshot = snapshot

    def read(self, now: datetime) -> SchedulerSnapshot:
        assert now == NOW
        return self.snapshot


def test_tick_bounds_new_jobs_but_replays_do_not_consume_new_job_budget() -> None:
    snapshot = SchedulerSnapshot(
        harvest_bindings=(
            binding("a"),
            binding("b"),
            binding("c"),
        ),
        delivery_schedules=(),
        known_jobs=(),
        input_gaps=(),
    )
    plan = PlanCatchupJobs()(snapshot, now=NOW)
    first_key = plan.jobs[0].business_key
    store = FakeStore({first_key})

    result = RunSchedulerTick(FakeInputs(snapshot), store)(
        now=NOW,
        max_new_jobs=1,
    )

    assert result.new_jobs == 1
    assert result.replayed_jobs == 1
    assert result.deferred_jobs == 1
    assert len(store.calls) == 2


def test_awaiting_first_window_is_not_rebuilt_when_clock_advances() -> None:
    schedule = binding("statistics")
    first = PlanCatchupJobs.harvest_job(schedule, NOW)
    snapshot = SchedulerSnapshot(
        harvest_bindings=(schedule,),
        delivery_schedules=(),
        known_jobs=(
            KnownWorkflowJob(
                first.business_key,
                "awaiting_external",
                "statistics",
            ),
        ),
        input_gaps=(),
    )

    later = PlanCatchupJobs()(snapshot, now=NOW + timedelta(minutes=5))

    assert later.jobs == ()
    assert later.coverage_gaps == (
        CoverageGap(
            "harvest",
            "statistics",
            "harvest_window_awaiting_external",
        ),
    )


def test_pubmed_first_window_uses_last_completed_utc_day_not_moving_24_hours() -> None:
    midday = NOW + timedelta(hours=12)
    snapshot = SchedulerSnapshot(
        harvest_bindings=(binding("badminton", source="pubmed"),),
        delivery_schedules=(),
        known_jobs=(),
        input_gaps=(),
    )

    plan = PlanCatchupJobs()(snapshot, now=midday)

    assert len(plan.jobs) == 1
    payload = PlanCatchupJobs.decode(plan.jobs[0].input_json)
    assert payload["source_id"] == "pubmed"
    assert payload["window_start"] == (NOW - timedelta(days=1)).isoformat()
    assert payload["window_end"] == NOW.isoformat()


def test_pubmed_cursor_must_remain_on_utc_day_boundaries() -> None:
    schedule = binding(
        "badminton",
        source="pubmed",
        last=NOW - timedelta(hours=12),
    )
    snapshot = SchedulerSnapshot(
        harvest_bindings=(schedule,),
        delivery_schedules=(),
        known_jobs=(),
        input_gaps=(),
    )

    with pytest.raises(WorkflowJobError, match="invalid_pubmed_harvest_window"):
        PlanCatchupJobs()(snapshot, now=NOW + timedelta(days=1))
