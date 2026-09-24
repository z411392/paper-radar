from typing import Protocol

from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture, CrossrefStoredCapture
from libs.discovery.dtos.crossref_page import CrossrefPageRequest


class CrossrefCaptureStorePort(Protocol):
    def save(
        self, request: CrossrefPageRequest, capture: CrossrefHttpCapture, *, attempt_key: str
    ) -> str: ...

    def read(self, receipt_id: str) -> CrossrefStoredCapture: ...
