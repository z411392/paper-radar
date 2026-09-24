from typing import Protocol

from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_claimed_capture import CrossrefClaimedCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan


class CaptureClaimedCrossrefResponsePort(Protocol):
    def __call__(
        self,
        plan: CrossrefWindowPlan,
        claim: CrossrefCaptureClaim,
    ) -> CrossrefClaimedCapture: ...
