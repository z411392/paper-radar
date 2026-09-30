from typing import Protocol

from libs.discovery.dtos.crossref_capture import CrossrefCaptureResult
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan


class CrossrefCapturePagePort(Protocol):
    def __call__(
        self,
        plan: CrossrefWindowPlan,
        request: CrossrefPageRequest,
        *,
        attempt_key: str,
    ) -> CrossrefCaptureResult: ...
