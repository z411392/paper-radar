from typing import Protocol

from libs.discovery.dtos.crossref_capture import CrossrefReplayedPage
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan


class CrossrefReplayCapturePort(Protocol):
    def __call__(
        self,
        plan: CrossrefWindowPlan,
        request: CrossrefPageRequest,
        receipt_id: str,
    ) -> CrossrefReplayedPage: ...
