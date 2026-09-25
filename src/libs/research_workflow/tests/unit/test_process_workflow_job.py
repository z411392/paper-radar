from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from libs.delivery.dtos.scheduled_digest import ScheduledDigestOutcome
from libs.discovery.exceptions.crossref_harvest_journal_error import (
    CrossrefHarvestJournalError,
)
from libs.research_workflow.application.commands.process_workflow_job import (
    ProcessWorkflowJob,
)
from libs.research_workflow.dtos.harvest_run_result import HarvestRunResult
from libs.research_workflow.dtos.workflow_job import (
    WorkflowJobCompletion,
    WorkflowJobLease,
)


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)


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
    progress = Mock(state="succeeded")
    runner = Mock(
        return_value=HarvestRunResult(
            progress=progress,
            stop_reason="complete",
            fetch_count=1,
            processed_pages=1,
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

    request = builder.call_args.args[0]
    assert request.profile_id == "personal"
    assert request.domain_id == "statistics"
    assert request.expected_profile_revision == 3
    assert request.expected_domain_revision == 7
    assert request.source_id == "arxiv"
    runner.assert_called_once_with(query, max_pages=10, retry_failed=True)
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
    pubmed = Mock(return_value=Mock(stop_reason="complete"))
    arxiv = Mock()
    command = ProcessWorkflowJob(
        store=store,
        builder=builder,
        harvest=arxiv,
        clock=Clock(),
        live_source_enabled=True,
        pubmed=pubmed,
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


def crossref_payload() -> str:
    return (
        '{"binding_key":"personal:3:statistics:7:crossref",'
        '"domain_id":"statistics","domain_revision":7,'
        '"profile_id":"personal","profile_revision":3,"source_id":"crossref",'
        '"window_end":"2026-09-24T00:00:00+00:00",'
        '"window_start":"2026-09-23T00:00:00+00:00"}'
    )


def test_crossref_journal_database_error_is_retryable_failed_job() -> None:
    store = Store(lease("harvest_window", crossref_payload()))
    builder = Mock(return_value=object())
    planner = Mock(return_value=object())
    crossref = Mock(
        side_effect=CrossrefHarvestJournalError(
            "crossref_journal_database_error"
        )
    )
    command = ProcessWorkflowJob(
        store=store,
        builder=builder,
        harvest=None,
        clock=Clock(),
        live_source_enabled=True,
        crossref_plan=planner,
        crossref=crossref,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "failed"
    completion = store.completed[0]
    assert completion.state == "failed"
    assert completion.error_code == "crossref_journal_database_error"
    assert completion.next_due_at == NOW + timedelta(minutes=5, seconds=2)


def test_crossref_item_outcome_conflict_awaits_external_repair() -> None:
    store = Store(lease("harvest_window", crossref_payload()))
    builder = Mock(return_value=object())
    planner = Mock(return_value=object())
    crossref = Mock(
        side_effect=CrossrefHarvestJournalError(
            "crossref_item_outcome_conflict"
        )
    )
    command = ProcessWorkflowJob(
        store=store,
        builder=builder,
        harvest=None,
        clock=Clock(),
        live_source_enabled=True,
        crossref_plan=planner,
        crossref=crossref,
    )

    result = command("worker:test", lease_seconds=60)

    assert result.state == "awaiting_external"
    completion = store.completed[0]
    assert completion.state == "awaiting_external"
    assert completion.error_code == "crossref_item_outcome_conflict"
    assert completion.next_due_at == NOW + timedelta(hours=1, seconds=2)
