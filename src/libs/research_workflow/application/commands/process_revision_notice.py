from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError
from libs.delivery.exceptions.prior_recipient_error import PriorRecipientError
from libs.delivery.exceptions.scheduled_digest_error import ScheduledDigestError
from libs.delivery.ports.delivery_dispatch_store_port import DeliveryDispatchStorePort
from libs.delivery.ports.dispatch_digest_port import DispatchDigestPort
from libs.delivery.ports.prepare_scheduled_digest_port import PrepareScheduledDigestPort
from libs.delivery.ports.prior_recipient_history_port import PriorRecipientHistoryPort
from libs.paper_explanations.exceptions.digest_summary_read_error import (
    DigestSummaryReadError,
)
from libs.paper_explanations.ports.read_digest_current_summary_port import (
    ReadDigestCurrentSummaryPort,
)
from libs.research_workflow.dtos.revision_notice import (
    RevisionNoticeOutcome,
    RevisionNoticeRequest,
)


class ProcessRevisionNotice:
    def __init__(
        self,
        *,
        store: DeliveryDispatchStorePort,
        summaries: ReadDigestCurrentSummaryPort,
        prior_recipient: PriorRecipientHistoryPort,
        dispatch: DispatchDigestPort,
        prepare: PrepareScheduledDigestPort | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self._summaries = summaries
        self._prior = prior_recipient
        self._dispatch = dispatch
        self._prepare = prepare
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

    def _eligibility(self, snapshot) -> RevisionNoticeOutcome | None:
        if snapshot.outbox_state != "pending":
            return self._terminal(snapshot.outbox_state)
        if snapshot.digest_state != "queued":
            return RevisionNoticeOutcome("failed", "delivery_state_corrupt")
        if not snapshot.enabled:
            return RevisionNoticeOutcome(
                "cancelled",
                "delivery_subscription_disabled",
            )

        for item in snapshot.items:
            if item.item_kind == "paper":
                if item.revision_id is None or item.summary_id is None:
                    return RevisionNoticeOutcome("failed", "delivery_digest_corrupt")
                summary = self._summaries(item.work_id, item.revision_id)
                if summary is None or summary.summary_id != item.summary_id:
                    return RevisionNoticeOutcome(
                        "cancelled",
                        "digest_current_input_stale",
                    )
                continue
            if item.item_kind == "status_notice":
                if item.event_kind not in {"correction", "retraction"}:
                    return RevisionNoticeOutcome("failed", "delivery_digest_corrupt")
                if not self._prior.contains(
                    snapshot.reader_id,
                    snapshot.channel,
                    item.work_id,
                ):
                    return RevisionNoticeOutcome(
                        "cancelled",
                        "status_notice_recipient_no_longer_eligible",
                    )
                continue
            return RevisionNoticeOutcome("failed", "delivery_digest_corrupt")
        return None

    def _dispatch_current(self, outbox_id: str) -> RevisionNoticeOutcome:
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

    @staticmethod
    def _context_matches(snapshot, rebuild) -> bool:
        return (
            rebuild.subscription_id == snapshot.subscription_id
            and snapshot.period_key is not None
            and rebuild.period_key == snapshot.period_key
            and rebuild.rebuild_reason is None
            and rebuild.rebuild_outbox_id is None
        )

    def __call__(self, request: RevisionNoticeRequest) -> RevisionNoticeOutcome:
        if not isinstance(request, RevisionNoticeRequest):
            return RevisionNoticeOutcome("failed", "invalid_revision_notice_request")
        try:
            snapshot = self._store.load_preflight(request.outbox_id)
            eligibility = self._eligibility(snapshot)
            if eligibility is None:
                return self._dispatch_current(request.outbox_id)

            if eligibility.state != "cancelled":
                return eligibility
            if snapshot.outbox_state != "pending":
                return eligibility
            if eligibility.error_code != "digest_current_input_stale":
                self._store.cancel_pending(request.outbox_id)
                return eligibility

            rebuild = request.rebuild_request
            if rebuild is not None and not self._context_matches(snapshot, rebuild):
                return RevisionNoticeOutcome(
                    "failed",
                    "digest_rebuild_context_mismatch",
                )

            self._store.cancel_pending(request.outbox_id)
            if rebuild is None or self._prepare is None:
                return RevisionNoticeOutcome(
                    "cancelled",
                    "digest_rebuild_context_missing",
                )

            rebuilt_request = replace(
                rebuild,
                rebuild_reason="current_input_stale",
                rebuild_outbox_id=request.outbox_id,
            )
            rebuilt = self._prepare(
                rebuilt_request,
                created_at=self._clock(),
            )
            if rebuilt.state == "empty":
                return RevisionNoticeOutcome("cancelled", "digest_rebuild_empty")
            if rebuilt.state == "disabled":
                return RevisionNoticeOutcome(
                    "cancelled",
                    "delivery_subscription_disabled",
                )
            if rebuilt.state != "queued":
                return RevisionNoticeOutcome("failed", "digest_rebuild_failed")
            if rebuilt.outbox_id != request.outbox_id:
                return RevisionNoticeOutcome(
                    "failed",
                    "digest_rebuild_target_mismatch",
                )

            refreshed = self._store.load_preflight(request.outbox_id)
            second = self._eligibility(refreshed)
            if second is not None:
                if second.state == "cancelled" and refreshed.outbox_state == "pending":
                    self._store.cancel_pending(request.outbox_id)
                    if second.error_code == "digest_current_input_stale":
                        return RevisionNoticeOutcome(
                            "cancelled",
                            "digest_rebuild_still_stale",
                        )
                return second
            return self._dispatch_current(request.outbox_id)
        except (
            DeliveryStoreError,
            PriorRecipientError,
            DigestSummaryReadError,
            ScheduledDigestError,
        ) as exc:
            code = getattr(exc, "code", None)
            if not isinstance(code, str) or not code:
                code = str(exc) or exc.__class__.__name__
            return RevisionNoticeOutcome("awaiting_external", code)
