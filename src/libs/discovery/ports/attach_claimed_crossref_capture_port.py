from typing import Protocol

from libs.discovery.dtos.crossref_attachment import CrossrefAttachment
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan


class AttachClaimedCrossrefCapturePort(Protocol):
    def __call__(
        self,
        plan: CrossrefWindowPlan,
        claim: CrossrefCaptureClaim,
    ) -> CrossrefAttachment: ...
