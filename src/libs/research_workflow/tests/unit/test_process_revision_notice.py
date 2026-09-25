from datetime import datetime, timezone
from unittest.mock import Mock

from libs.delivery.dtos.delivery_dispatch import (
    DeliveryPreflightItem,
    DeliveryPreflightSnapshot,
    DispatchOutcome,
)
from libs.paper_explanations.dtos.digest_current_summary import DigestCurrentSummary
from libs.research_workflow.application.commands.process_revision_notice import (
    ProcessRevisionNotice,
)


NOW = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.cancelled = []

    def load_preflight(self, outbox_id):
        assert outbox_id == self.snapshot.outbox_id
        return self.snapshot

    def cancel_pending(self, outbox_id):
        self.cancelled.append(outbox_id)
        return "cancelled"


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


def command(store, *, summary=None, prior=True, dispatch_state="provider_accepted"):
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

    result = usecase("outbox:test")

    assert result.state == "succeeded"
    summaries.assert_called_once_with("work:paper", "revision:paper")
    history.contains.assert_called_once_with("reader:test", "email", "work:status")
    dispatch.assert_called_once_with("outbox:test", now=NOW)
    assert store.cancelled == []


def test_stale_paper_is_cancelled_before_dispatch():
    store = Store(snapshot(paper()))
    usecase, _, history, dispatch = command(store, summary=None)

    result = usecase("outbox:test")

    assert (result.state, result.error_code) == (
        "cancelled",
        "digest_current_input_stale",
    )
    assert store.cancelled == ["outbox:test"]
    history.contains.assert_not_called()
    dispatch.assert_not_called()


def test_status_non_prior_recipient_is_cancelled_without_summary_lookup():
    store = Store(snapshot(status()))
    usecase, summaries, _, dispatch = command(store, prior=False)

    result = usecase("outbox:test")

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

    result = usecase("outbox:test")

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

    result = usecase("outbox:test")

    assert (result.state, result.error_code) == (
        "awaiting_external",
        "delivery_unknown_no_provider_lookup",
    )
    summaries.assert_not_called()
    history.contains.assert_not_called()
    dispatch.assert_not_called()
