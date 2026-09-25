from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from libs.delivery.dtos.scheduled_digest import ScheduledDigestOutcome
from libs.research_workflow.application.commands.process_workflow_job import (
    ProcessWorkflowJob,
)
from libs.research_workflow.dtos.harvest_run_result import HarvestRunResult
from libs.research_workflow.dtos.workflow_job import (
    WorkflowJobCompletion,
    WorkflowJobLease,
)


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
UNIT = "unit:" + "d" * 64


class Clock:
    def __init__(self) -> None:
        self.values = [NOW, NOW + timedelta(seconds=2)]

    def now(self):
        return self.values.pop(0) if self.values else NOW + timedelta(seconds=2)


class Store:
    def __init__(self, lease: WorkflowJobLease | None) -> None:
        self.lease = lease
        self.completed = []

    def claim_due(self, owner_id, *, now, lease_seconds):
        assert owner_id == "worker:test"
        assert now == NOW
        assert lease_seconds == 60
        lease, self.lease = self.lease, None
        return lease

    def complete(self, request):
        self.completed.append(request)
        return WorkflowJobCompletion(
            request.job_id,
            request.state,
            request.fencing_token,
            False,
        )


def lease(job_kind: str, payload: str) -> WorkflowJobLease:
    return WorkflowJobLease(
        job_id="job:" + "a" * 64,
        job_kind=job_kind,
        business_key="business:test",
        input_json=payload,
        input_fingerprint="b" * 64,
        owner_id="worker:test",
        fencing_token=3,
        attempt_id="job-attempt:" + "c" * 64,
        attempt_no=2,
        lease_until=NOW + timedelta(seconds=60),
    )


def harvest_payload() -> str:
    return (
        '{"binding_key":"personal:3:statistics:7:arxiv",'
        '"domain_id":"statistics","domain_revision":7,'
        '"profile_id":"personal","profile_revision":3,"source_id":"arxiv",'
        '"window_end":"2026-09-24T00:00:00+00:00",'
        '"window_start":"2026-09-23T00:00:00+00:00"}'
    )


def test_live_source_not_authorized_never_calls_harvest_ports() -> None:
    store = Store(lease("harvest_window", harvest_payload()))
    builder = Mock()
    runner = Mock()
    command = ProcessWorkflowJob(
        store=store,
        builder=builder,
        harvest=runner,
        clock=Clock(),
        live_source_enabled=False,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "awaiting_external"
    builder.assert_not_called()
    runner.assert_not_called()
    completion = store.completed[0]
    assert completion.state == "awaiting_external"
    assert completion.error_code == "live_source_not_authorized"
    assert completion.next_due_at == NOW + timedelta(hours=1, seconds=2)


def test_harvest_job_uses_exact_scheduled_revisions_and_completes() -> None:
    store = Store(lease("harvest_window", harvest_payload()))
    query = object()
    builder = Mock(return_value=query)
    progress = Mock(state="succeeded", unit_id=UNIT)
    runner = Mock(
        return_value=HarvestRunResult(
            progress=progress,
            stop_reason="complete",
            fetch_count=1,
            processed_pages=1,
            reused_captures=0,
        )
    )
    source_catalog = Mock(return_value=Mock(state="succeeded"))
    command = ProcessWorkflowJob(
        store=store,
        builder=builder,
        harvest=runner,
        clock=Clock(),
        live_source_enabled=True,
        source_catalog=source_catalog,
    )

    result = command("worker:test", lease_seconds=60)

    request = builder.call_args.args[0]
    assert request.profile_id == "personal"
    assert request.domain_id == "statistics"
    assert request.expected_profile_revision == 3
    assert request.expected_domain_revision == 7
    assert request.source_id == "arxiv"
    runner.assert_called_once_with(query, max_pages=10, retry_failed=True)
    source_catalog.assert_called_once_with(
        "arxiv",
        UNIT,
        max_observations=100,
        projected_at=NOW + timedelta(seconds=2),
    )
    assert result.state == "succeeded"
    assert store.completed[0].state == "succeeded"
    assert store.completed[0].next_due_at is None


def test_incomplete_harvest_is_retryable_and_does_not_claim_success() -> None:
    store = Store(lease("harvest_window", harvest_payload()))
    builder = Mock(return_value=object())
    runner = Mock(
        return_value=HarvestRunResult(
            progress=Mock(state="partial"),
            stop_reason="page_budget",
            fetch_count=10,
            processed_pages=10,
            reused_captures=0,
        )
    )
    command = ProcessWorkflowJob(
        store=store,
        builder=builder,
        harvest=runner,
        clock=Clock(),
        live_source_enabled=True,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "failed"
    completion = store.completed[0]
    assert completion.state == "failed"
    assert completion.error_code == "harvest_page_budget"
    assert completion.next_due_at == NOW + timedelta(minutes=1, seconds=2)


def test_prepare_digest_fails_closed_until_candidate_pipeline_is_connected() -> None:
    payload = (
        '{"coverage_gaps":[],"cutoff_at":"2026-09-24T00:00:00+00:00",'
        '"period_key":"2026-09-24","period_start":"2026-09-23T00:00:00+00:00",'
        '"subscription_id":"subscription:daily"}'
    )
    store = Store(lease("prepare_digest", payload))
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(),
        harvest=Mock(),
        clock=Clock(),
        live_source_enabled=False,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "awaiting_external"
    assert store.completed[0].error_code == "digest_candidate_pipeline_not_connected"
    assert store.completed[0].next_due_at == NOW + timedelta(hours=1, seconds=2)


def test_no_due_job_is_idle_and_has_no_completion() -> None:
    store = Store(None)
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(),
        harvest=Mock(),
        clock=Clock(),
        live_source_enabled=False,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "idle"
    assert store.completed == []


def test_connected_prepare_digest_job_completes_without_smtp_dispatch() -> None:
    payload = (
        '{"coverage_gaps":[{"identity":"badminton:pubmed","kind":"harvest",'
        '"reason":"source_scheduler_not_supported"}],'
        '"cutoff_at":"2026-09-24T00:00:00+00:00",'
        '"period_key":"2026-09-24","period_start":"2026-09-23T00:00:00+00:00",'
        '"subscription_id":"subscription:daily"}'
    )
    store = Store(lease("prepare_digest", payload))
    digest = Mock(
        return_value=ScheduledDigestOutcome(
            "queued",
            1,
            "digest-record:test",
            "outbox:test",
        )
    )
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(),
        harvest=None,
        clock=Clock(),
        live_source_enabled=False,
        digest=digest,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "succeeded"
    request = digest.call_args.args[0]
    assert request.subscription_id == "subscription:daily"
    assert request.period_key == "2026-09-24"
    assert request.period_start == NOW - timedelta(days=1)
    assert request.cutoff_at == NOW
    assert request.coverage_gaps[0].identity == "badminton:pubmed"
    assert store.completed[0].state == "succeeded"


def test_pubmed_job_uses_dedicated_runner_and_create_date_basis() -> None:
    payload = (
        '{"binding_key":"personal:3:badminton:1:pubmed",'
        '"domain_id":"badminton","domain_revision":1,'
        '"profile_id":"personal","profile_revision":3,"source_id":"pubmed",'
        '"window_end":"2026-09-24T00:00:00+00:00",'
        '"window_start":"2026-09-23T00:00:00+00:00"}'
    )
    store = Store(lease("harvest_window", payload))
    query = object()
    builder = Mock(return_value=query)
    pubmed = Mock(
        return_value=Mock(
            stop_reason="complete",
            progress=Mock(state="succeeded", unit_id=UNIT),
        )
    )
    arxiv = Mock()
    source_catalog = Mock(return_value=Mock(state="succeeded"))
    command = ProcessWorkflowJob(
        store=store,
        builder=builder,
        harvest=arxiv,
        clock=Clock(),
        live_source_enabled=True,
        pubmed=pubmed,
        source_catalog=source_catalog,
    )

    result = command("worker:test", lease_seconds=60)

    request = builder.call_args.args[0]
    assert request.source_id == "pubmed"
    assert request.time_basis == "createDate"
    assert request.expected_profile_revision == 3
    assert request.expected_domain_revision == 1
    pubmed.assert_called_once()
    assert pubmed.call_args.args == (query,)
    assert pubmed.call_args.kwargs["max_batches"] == 10
    arxiv.assert_not_called()
    source_catalog.assert_called_once_with(
        "pubmed",
        UNIT,
        max_observations=100,
        projected_at=NOW + timedelta(seconds=2),
    )
    assert result.state == "succeeded"


def test_pubmed_job_without_pubmed_runtime_is_awaiting_not_arxiv_fallback() -> None:
    payload = (
        '{"binding_key":"personal:3:badminton:1:pubmed",'
        '"domain_id":"badminton","domain_revision":1,'
        '"profile_id":"personal","profile_revision":3,"source_id":"pubmed",'
        '"window_end":"2026-09-24T00:00:00+00:00",'
        '"window_start":"2026-09-23T00:00:00+00:00"}'
    )
    store = Store(lease("harvest_window", payload))
    arxiv = Mock()
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(return_value=object()),
        harvest=arxiv,
        clock=Clock(),
        live_source_enabled=True,
        pubmed=None,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "awaiting_external"
    assert store.completed[0].error_code == "pubmed_runtime_not_connected"
    arxiv.assert_not_called()


def test_dispatch_digest_without_revision_notice_runtime_is_awaiting_external() -> None:
    store = Store(
        lease(
            "dispatch_digest",
            '{"outbox_id":"outbox:test","prepare_digest":null}',
        )
    )
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(),
        harvest=None,
        clock=Clock(),
        live_source_enabled=False,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "awaiting_external"
    assert store.completed[0].error_code == "revision_notice_runtime_not_connected"


def test_dispatch_digest_uses_formal_revision_notice_port() -> None:
    store = Store(
        lease(
            "dispatch_digest",
            '{"outbox_id":"outbox:test","prepare_digest":null}',
        )
    )
    notice = Mock(return_value=Mock(state="succeeded", error_code=None))
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(),
        harvest=None,
        clock=Clock(),
        live_source_enabled=False,
        revision_notice=notice,
    )

    result = command("worker:test", lease_seconds=60)

    notice.assert_called_once()
    request = notice.call_args.args[0]
    assert request.outbox_id == "outbox:test"
    assert request.rebuild_request is None
    assert result.state == "succeeded"
    assert store.completed[0].state == "succeeded"


def test_dispatch_digest_preserves_exact_prepare_context_for_rebuild() -> None:
    payload = (
        '{"outbox_id":"outbox:test","prepare_digest":{'
        '"subscription_id":"subscription:daily",'
        '"period_key":"2026-09-25",'
        '"period_start":"2026-09-23T00:00:00+00:00",'
        '"cutoff_at":"2026-09-25T00:00:00+00:00",'
        '"coverage_gaps":[{"kind":"harvest","identity":"x","reason":"gap"}]}}'
    )
    store = Store(lease("dispatch_digest", payload))
    notice = Mock(return_value=Mock(state="succeeded", error_code=None))
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(),
        harvest=None,
        clock=Clock(),
        live_source_enabled=False,
        revision_notice=notice,
    )

    result = command("worker:test", lease_seconds=60)

    request = notice.call_args.args[0]
    assert request.outbox_id == "outbox:test"
    assert request.rebuild_request is not None
    assert request.rebuild_request.subscription_id == "subscription:daily"
    assert request.rebuild_request.period_key == "2026-09-25"
    assert request.rebuild_request.period_start == NOW - timedelta(days=2)
    assert request.rebuild_request.cutoff_at == NOW
    assert request.rebuild_request.coverage_gaps[0].reason == "gap"
    assert result.state == "succeeded"


def test_complete_arxiv_harvest_without_catalog_projector_is_not_success() -> None:
    store = Store(lease("harvest_window", harvest_payload()))
    runner = Mock(
        return_value=HarvestRunResult(
            progress=Mock(state="succeeded", unit_id=UNIT),
            stop_reason="complete",
            fetch_count=0,
            processed_pages=0,
            reused_captures=1,
        )
    )
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(return_value=object()),
        harvest=runner,
        clock=Clock(),
        live_source_enabled=True,
        source_catalog=None,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "awaiting_external"
    assert store.completed[0].error_code == "source_catalog_projection_not_connected"


def test_catalog_projection_budget_keeps_harvest_job_retryable() -> None:
    store = Store(lease("harvest_window", harvest_payload()))
    runner = Mock(
        return_value=HarvestRunResult(
            progress=Mock(state="succeeded", unit_id=UNIT),
            stop_reason="complete",
            fetch_count=0,
            processed_pages=0,
            reused_captures=1,
        )
    )
    projector = Mock(return_value=Mock(state="budget_exhausted"))
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(return_value=object()),
        harvest=runner,
        clock=Clock(),
        live_source_enabled=True,
        source_catalog=projector,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "failed"
    assert store.completed[0].error_code == "source_catalog_projection_budget"
    assert store.completed[0].next_due_at == NOW + timedelta(minutes=1, seconds=2)


def test_verified_empty_harvest_does_not_require_catalog_projector() -> None:
    store = Store(lease("harvest_window", harvest_payload()))
    runner = Mock(
        return_value=HarvestRunResult(
            progress=Mock(state="verified_empty", unit_id=UNIT),
            stop_reason="complete",
            fetch_count=0,
            processed_pages=1,
            reused_captures=0,
        )
    )
    command = ProcessWorkflowJob(
        store=store,
        builder=Mock(return_value=object()),
        harvest=runner,
        clock=Clock(),
        live_source_enabled=True,
        source_catalog=None,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "succeeded"
    assert store.completed[0].state == "succeeded"
