from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_attachment import CrossrefAttachment
from libs.discovery.dtos.crossref_capture import CrossrefStoredCapture
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_capture_resolution import CrossrefCaptureResolution
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision


class ClaimedCrossrefAttachmentPort(Protocol):
    def replay(
        self, claim: CrossrefCaptureClaim, stored: CrossrefStoredCapture,
    ) -> CrossrefAttachment | None: ...

    def attach(
        self, claim: CrossrefCaptureClaim, stored: CrossrefStoredCapture,
        decision: CrossrefRateDecision, *, attached_at: datetime,
    ) -> CrossrefAttachment: ...

    def resolve(
        self, claim: CrossrefCaptureClaim, stored: CrossrefStoredCapture, *,
        resolved_at: datetime,
    ) -> CrossrefCaptureResolution: ...


class PublishClaimedCrossrefCapturePort(Protocol):
    def __call__(self, claim: CrossrefCaptureClaim) -> CrossrefStoredCapture: ...
