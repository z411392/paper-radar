from datetime import datetime, timezone
from unittest.mock import Mock

from libs.delivery.dtos.delivery_dispatch import (
    DeliveryPreflightItem,
    DeliveryPreflightSnapshot,
    DispatchOutcome,
)
from libs.paper_explanations.dtos.digest_current_summary import DigestCurrentSummary
from libs.delivery.dtos.scheduled_digest import (
    ScheduledDigestOutcome,
    ScheduledDigestRequest,
)
from libs.research_workflow.application.commands.process_revision_notice import (
    ProcessRevisionNotice,
)
from libs.research_workflow.dtos.revision_notice import RevisionNoticeRequest


NOW = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self, snapshot):
        self.snapshots = [snapshot]
        self.cancelled = []
        self.cancel_result = "cancelled"

    def load_preflight(self, outbox_id):
        snapshot = self.snapshots[0]
        assert outbox_id == snapshot.outbox_id
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return snapshot

    def cancel_pending(self, outbox_id):
        self.cancelled.append(outbox_id)
        return self.cancel_result


def snapshot(*items, enabled=True, outbox_state="pending", digest_state="queued"):
    return DeliveryPreflightSnapshot(
        "outbox:test",
        "digest:test",
        "subscription:test",
        "reader:test",
        "email",
        enabled,
        outbox_state,
        digest_state,
        tuple(items),
        "2026-09-25",
    )


def paper():
    return DeliveryPreflightItem(
        "event:paper",
        "work:paper",
        "summary:paper",
        "revision:paper",
        "paper",
        "new_work",
    )


def status():
    return DeliveryPreflightItem(
        "event:correction",
        "work:status",
        None,
        None,
        "status_notice",
        "correction",
    )


def command(
    store,
    *,
    summary=None,
    prior=True,
    dispatch_state="provider_accepted",
    prepare=None,
):
    summaries = Mock(return_value=summary)
    history = Mock()
    history.contains.return_value = prior
    dispatch = Mock(
        return_value=DispatchOutcome(dispatch_state, "outbox:test")
    )
    usecase = ProcessRevisionNotice(
        store=store,
        summaries=summaries,
        prior_recipient=history,
        dispatch=dispatch,
        prepare=prepare,
        clock=lambda: NOW,
    )
    return usecase, summaries, history, dispatch


def test_current_paper_and_prior_status_use_only_formal_dispatch_boundary():
    store = Store(snapshot(paper(), status()))
    summary = DigestCurrentSummary(
        "work:paper",
        "revision:paper",
        "summary:paper",
        "snapshot:paper",
        ("text",),
    )
    usecase, summaries, history, dispatch = command(store, summary=summary)

    result = usecase(RevisionNoticeRequest("outbox:test"))

    assert result.state == "succeeded"
    summaries.assert_called_once_with("work:paper", "revision:paper")
    history.contains.assert_called_once_with("reader:test", "email", "work:status")
    dispatch.assert_called_once_with("outbox:test", now=NOW)
    assert store.cancelled == []


def test_stale_legacy_paper_is_cancelled_without_guessing_window():
    store = Store(snapshot(paper()))
    usecase, _, history, dispatch = command(store, summary=None)

    result = usecase(RevisionNoticeRequest("outbox:test"))

    assert (result.state, result.error_code) == (
        "cancelled",
        "digest_rebuild_context_missing",
    )
    assert store.cancelled == ["outbox:test"]
    history.contains.assert_not_called()
    dispatch.assert_not_called()


def test_status_non_prior_recipient_is_cancelled_without_summary_lookup():
    store = Store(snapshot(status()))
    usecase, summaries, _, dispatch = command(store, prior=False)

    result = usecase(RevisionNoticeRequest("outbox:test"))

    assert (result.state, result.error_code) == (
        "cancelled",
        "status_notice_recipient_no_longer_eligible",
    )
    assert store.cancelled == ["outbox:test"]
    summaries.assert_not_called()
    dispatch.assert_not_called()


def test_disabled_subscription_is_cancelled_before_dispatch():
    store = Store(snapshot(paper(), enabled=False))
    usecase, summaries, history, dispatch = command(store)

    result = usecase(RevisionNoticeRequest("outbox:test"))

    assert (result.state, result.error_code) == (
        "cancelled",
        "delivery_subscription_disabled",
    )
    assert store.cancelled == ["outbox:test"]
    summaries.assert_not_called()
    history.contains.assert_not_called()
    dispatch.assert_not_called()


def test_unknown_delivery_is_never_automatically_resent():
    store = Store(snapshot(status(), outbox_state="unknown", digest_state="unknown"))
    usecase, summaries, history, dispatch = command(store)

    result = usecase(RevisionNoticeRequest("outbox:test"))

    assert (result.state, result.error_code) == (
        "awaiting_external",
        "delivery_unknown_no_provider_lookup",
    )
    summaries.assert_not_called()
    history.contains.assert_not_called()
    dispatch.assert_not_called()


def rebuild_request() -> ScheduledDigestRequest:
    return ScheduledDigestRequest(
        "subscription:test",
        "2026-09-25",
        datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc),
        NOW,
        (),
    )


def test_stale_paper_rebuilds_once_then_dispatches_current_snapshot():
    stale = snapshot(paper())
    current = snapshot(paper())
    store = Store(stale)
    store.snapshots.append(current)
    summary = DigestCurrentSummary(
        "work:paper",
        "revision:paper",
        "summary:paper",
        "snapshot:paper",
        ("text",),
    )
    summaries = Mock(side_effect=[None, summary])
    history = Mock()
    dispatch = Mock(return_value=DispatchOutcome("provider_accepted", "outbox:test"))
    prepare = Mock(
        return_value=ScheduledDigestOutcome(
            "queued",
            1,
            "digest:test",
            "outbox:test",
        )
    )
    usecase = ProcessRevisionNotice(
        store=store,
        summaries=summaries,
        prior_recipient=history,
        dispatch=dispatch,
        prepare=prepare,
        clock=lambda: NOW,
    )

    result = usecase(
        RevisionNoticeRequest("outbox:test", rebuild_request())
    )

    assert result.state == "succeeded"
    assert store.cancelled == ["outbox:test"]
    prepared = prepare.call_args.args[0]
    assert prepared.rebuild_reason == "current_input_stale"
    assert prepared.rebuild_outbox_id == "outbox:test"
    assert prepare.call_count == 1
    dispatch.assert_called_once_with("outbox:test", now=NOW)


def test_stale_legacy_job_cancels_without_guessing_rebuild_window():
    store = Store(snapshot(paper()))
    usecase, _, _, dispatch = command(store, summary=None)

    result = usecase(RevisionNoticeRequest("outbox:test"))

    assert (result.state, result.error_code) == (
        "cancelled",
        "digest_rebuild_context_missing",
    )
    assert store.cancelled == ["outbox:test"]
    dispatch.assert_not_called()


def test_mismatched_rebuild_context_fails_before_cancelling_slot():
    store = Store(snapshot(paper()))
    wrong = ScheduledDigestRequest(
        "subscription:other",
        "2026-09-25",
        datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc),
        NOW,
        (),
    )
    prepare = Mock()
    usecase, _, _, dispatch = command(
        store,
        summary=None,
        prepare=prepare,
    )

    result = usecase(RevisionNoticeRequest("outbox:test", wrong))

    assert (result.state, result.error_code) == (
        "failed",
        "digest_rebuild_context_mismatch",
    )
    assert store.cancelled == []
    prepare.assert_not_called()
    dispatch.assert_not_called()


def test_rebuild_still_stale_is_cancelled_without_second_rebuild():
    store = Store(snapshot(paper()))
    store.snapshots.append(snapshot(paper()))
    prepare = Mock(
        return_value=ScheduledDigestOutcome(
            "queued",
            1,
            "digest:test",
            "outbox:test",
        )
    )
    usecase, _, _, dispatch = command(
        store,
        summary=None,
        prepare=prepare,
    )

    result = usecase(
        RevisionNoticeRequest("outbox:test", rebuild_request())
    )

    assert (result.state, result.error_code) == (
        "cancelled",
        "digest_rebuild_still_stale",
    )
    assert store.cancelled == ["outbox:test", "outbox:test"]
    assert prepare.call_count == 1
    dispatch.assert_not_called()


def test_status_recipient_loss_never_uses_rebuild_authority():
    store = Store(snapshot(status()))
    prepare = Mock()
    usecase, _, _, dispatch = command(
        store,
        prior=False,
        prepare=prepare,
    )

    result = usecase(
        RevisionNoticeRequest("outbox:test", rebuild_request())
    )

    assert (result.state, result.error_code) == (
        "cancelled",
        "status_notice_recipient_no_longer_eligible",
    )
    assert store.cancelled == ["outbox:test"]
    prepare.assert_not_called()
    dispatch.assert_not_called()


def test_cancel_race_to_sending_never_reports_cancelled_or_rebuilds():
    store = Store(snapshot(paper()))
    store.cancel_result = "sending"
    prepare = Mock()
    usecase, _, _, dispatch = command(
        store,
        summary=None,
        prepare=prepare,
    )

    result = usecase(
        RevisionNoticeRequest("outbox:test", rebuild_request())
    )

    assert (result.state, result.error_code) == (
        "awaiting_external",
        "inflight_delivery_without_terminal_receipt",
    )
    assert store.cancelled == ["outbox:test"]
    prepare.assert_not_called()
    dispatch.assert_not_called()
