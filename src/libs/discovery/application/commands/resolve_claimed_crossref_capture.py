"""Resolve already-attached Crossref evidence locally; never authorize provider I/O."""
from collections.abc import Callable
from datetime import datetime, timezone

from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_capture_resolution import CrossrefCaptureResolution
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.exceptions.crossref_attachment_error import CrossrefAttachmentError as Error
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.ports.claimed_crossref_attachment_port import (
    ClaimedCrossrefAttachmentPort,
    PublishClaimedCrossrefCapturePort,
)
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort


class ResolveClaimedCrossrefCapture:
    def __init__(
        self,
        publish: PublishClaimedCrossrefCapturePort,
        attachments: ClaimedCrossrefAttachmentPort,
        *,
        source: CrossrefPageSourcePort,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._publish = publish
        self._attachments = attachments
        self._source = source
        self._clock = clock

    def __call__(
        self,
        plan: CrossrefWindowPlan,
        claim: CrossrefCaptureClaim,
    ) -> CrossrefCaptureResolution:
        try:
            if (
                not isinstance(claim, CrossrefCaptureClaim)
                or self._source.page(plan, claim.request.cursor) != claim.request
            ):
                raise Error("crossref_resolution_request_mismatch")
        except CrossrefProtocolError as exc:
            raise Error("crossref_resolution_request_mismatch") from exc
        stored = self._publish(claim)
        return self._attachments.resolve(
            claim,
            stored,
            resolved_at=self._clock(),
        )
