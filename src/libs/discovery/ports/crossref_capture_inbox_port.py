from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_capture_inbox import CrossrefCaptureInboxRecord


class CrossrefCaptureInboxPort(Protocol):
    def stage(self, claim: CrossrefCaptureClaim, capture: CrossrefHttpCapture, *,
              staged_at: datetime) -> CrossrefCaptureInboxRecord: ...

    def load(self, claim: CrossrefCaptureClaim) -> CrossrefCaptureInboxRecord | None: ...
