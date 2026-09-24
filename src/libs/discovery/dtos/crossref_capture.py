from dataclasses import dataclass, field
from datetime import datetime

from libs.discovery.dtos.crossref_page import CrossrefDecodedPage, CrossrefPageRequest
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision


@dataclass(frozen=True)
class CrossrefHttpCapture:
    """Transfer-decoded, content-coded body or prefix; never TLS/HTTP framing bytes."""
    status: int | None
    headers: tuple[tuple[str, str], ...]
    body: bytes = field(repr=False)
    received_at: datetime
    complete: bool
    capture_error: str | None


@dataclass(frozen=True)
class CrossrefStoredCapture:
    receipt_id: str
    attempt_key: str
    request: CrossrefPageRequest
    capture: CrossrefHttpCapture
    body_object_id: str
    body_sha256: str


@dataclass(frozen=True)
class CrossrefCaptureResult:
    receipt_id: str
    decision: CrossrefRateDecision


@dataclass(frozen=True)
class CrossrefReplayedPage:
    receipt_id: str
    content_coded_sha256: str
    entity_sha256: str
    page: CrossrefDecodedPage
