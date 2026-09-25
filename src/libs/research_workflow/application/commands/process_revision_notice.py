from collections.abc import Callable
from datetime import datetime, timezone

from libs.delivery.ports.dispatch_digest_port import DispatchDigestPort
from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError
from libs.delivery.exceptions.prior_recipient_error import PriorRecipientError
from libs.delivery.ports.delivery_dispatch_store_port import DeliveryDispatchStorePort
from libs.delivery.ports.prior_recipient_history_port import PriorRecipientHistoryPort
from libs.paper_explanations.exceptions.digest_summary_read_error import (
    DigestSummaryReadError,
)
from libs.paper_explanations.ports.read_digest_current_summary_port import (
    ReadDigestCurrentSummaryPort,
)
from libs.research_workflow.dtos.revision_notice import RevisionNoticeOutcome


class ProcessRevisionNotice:
    def __init__(
        self,
        *,
        store: DeliveryDispatchStorePort,
        summaries: ReadDigestCurrentSummaryPort,
        prior_recipient: PriorRecipientHistoryPort,
        dispatch: DispatchDigestPort,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self._summaries = summaries
        self._prior = prior_recipient
        self._dispatch = dispatch
        self._clock = clock

    @staticmethod
    def _terminal(state: str) -> RevisionNoticeOutcome:
        if state == "provider_accepted":
            return RevisionNoticeOutcome("succeeded")
        if state == "cancelled":
            return RevisionNoticeOutcome("cancelled", "delivery_cancelled")
        if state in {"unknown", "sending"}:
            return RevisionNoticeOutcome(
                "awaiting_external",
                "delivery_unknown_no_provider_lookup"
                if state == "unknown"
                else "inflight_delivery_without_terminal_receipt",
            )
        if state == "failed":
            return RevisionNoticeOutcome("failed", "delivery_rejected")
        return RevisionNoticeOutcome("failed", "delivery_state_corrupt")

    def __call__(self, outbox_id: str) -> RevisionNoticeOutcome:
        try:
            snapshot = self._store.load_preflight(outbox_id)
            if snapshot.outbox_state != "pending":
                return self._terminal(snapshot.outbox_state)
            if snapshot.digest_state != "queued":
                return RevisionNoticeOutcome("failed", "delivery_state_corrupt")
            if not snapshot.enabled:
                self._store.cancel_pending(outbox_id)
                return RevisionNoticeOutcome(
                    "cancelled",
                    "delivery_subscription_disabled",
                )

            for item in snapshot.items:
                if item.item_kind == "paper":
                    assert item.revision_id is not None
                    assert item.summary_id is not None
                    summary = self._summaries(item.work_id, item.revision_id)
                    if summary is None or summary.summary_id != item.summary_id:
                        self._store.cancel_pending(outbox_id)
                        return RevisionNoticeOutcome(
                            "cancelled",
                            "digest_current_input_stale",
                        )
                    continue
                if item.item_kind == "status_notice":
                    if item.event_kind not in {"correction", "retraction"}:
                        return RevisionNoticeOutcome(
                            "failed",
                            "delivery_digest_corrupt",
                        )
                    if not self._prior.contains(
                        snapshot.reader_id,
                        snapshot.channel,
                        item.work_id,
                    ):
                        self._store.cancel_pending(outbox_id)
                        return RevisionNoticeOutcome(
                            "cancelled",
                            "status_notice_recipient_no_longer_eligible",
                        )
                    continue
                return RevisionNoticeOutcome("failed", "delivery_digest_corrupt")

            dispatched = self._dispatch(outbox_id, now=self._clock())
            if dispatched.state == "provider_accepted":
                return RevisionNoticeOutcome("succeeded")
            if dispatched.state == "cancelled":
                return RevisionNoticeOutcome("cancelled", "delivery_cancelled")
            if dispatched.state in {"unknown", "sending", "effects_disabled"}:
                code = (
                    "delivery_effects_disabled"
                    if dispatched.state == "effects_disabled"
                    else "delivery_unknown_no_provider_lookup"
                    if dispatched.state == "unknown"
                    else "inflight_delivery_without_terminal_receipt"
                )
                return RevisionNoticeOutcome("awaiting_external", code)
            if dispatched.state == "recipient_missing":
                return RevisionNoticeOutcome(
                    "awaiting_external",
                    "delivery_recipient_missing",
                )
            if dispatched.state == "payload_mismatch":
                return RevisionNoticeOutcome(
                    "awaiting_external",
                    "delivery_payload_mismatch",
                )
            if dispatched.state == "failed":
                return RevisionNoticeOutcome("failed", "delivery_rejected")
            return RevisionNoticeOutcome("failed", "delivery_state_corrupt")
        except (
            DeliveryStoreError,
            PriorRecipientError,
            DigestSummaryReadError,
        ) as exc:
            code = str(exc) or exc.__class__.__name__
            return RevisionNoticeOutcome("awaiting_external", code)
