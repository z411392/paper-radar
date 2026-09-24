"""Local publication and rate reconciliation followed by fenced journal attachment.

No transport/parser, no checkpoint advancement, no dispatch-claim release. Gate state
may be stricter after a failed attach: it must never be rolled back with local journal SQL.
"""
from collections.abc import Callable
from datetime import datetime, timezone

from libs.discovery.dtos.crossref_attachment import CrossrefAttachment
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.exceptions.crossref_attachment_error import CrossrefAttachmentError as Error
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.ports.claimed_crossref_attachment_port import (
    ClaimedCrossrefAttachmentPort,
    PublishClaimedCrossrefCapturePort,
)
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort
from libs.discovery.ports.crossref_rate_gate_port import CrossrefRateGatePort


class AttachClaimedCrossrefCapture:
    def __init__(
        self,
        publish: PublishClaimedCrossrefCapturePort,
        gate: CrossrefRateGatePort,
        attachments: ClaimedCrossrefAttachmentPort,
        *,
        source: CrossrefPageSourcePort,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._source = source
        self._publish = publish
        self._gate = gate
        self._attachments = attachments
        self._clock = clock

    def __call__(self, plan: CrossrefWindowPlan, claim: CrossrefCaptureClaim) -> CrossrefAttachment:
        try:
            if (not isinstance(claim, CrossrefCaptureClaim)
                    or self._source.page(plan, claim.request.cursor) != claim.request):
                raise Error('crossref_attachment_request_mismatch')
        except CrossrefProtocolError as exc:
            raise Error('crossref_attachment_request_mismatch') from exc
        stored = self._publish(claim)
        prior = self._attachments.replay(claim, stored)
        if prior is not None:
            return prior
        with self._gate.slot(plan.definition.contact_email) as lease:
            decision = lease.observe(stored.capture.status, stored.capture.headers,
                                     capture_error=stored.capture.capture_error)
        return self._attachments.attach(claim, stored, decision, attached_at=self._clock())
