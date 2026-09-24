from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_capture_resolution import CrossrefCaptureResolution
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan


class ResolveClaimedCrossrefCapturePort(Protocol):
    def __call__(
        self,
        plan: CrossrefWindowPlan,
        claim: CrossrefCaptureClaim,
    ) -> CrossrefCaptureResolution: ...
