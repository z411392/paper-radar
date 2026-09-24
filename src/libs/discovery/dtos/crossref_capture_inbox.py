from dataclasses import dataclass

from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefPageRequest


@dataclass(frozen=True)
class CrossrefCaptureInboxRecord:
    claim_id: str
    request: CrossrefPageRequest
    capture: CrossrefHttpCapture
    envelope_sha256: str
    body_sha256: str
