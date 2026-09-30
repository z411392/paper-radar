from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AccessLocationClaim:
    manifestation_id: str
    evidence_source_id: str
    location_url: str
    content_scope: str
    reader_access_signal: str
    automated_retrieval_signal: str
    permitted_uses: tuple[str, ...]
    license_id: str | None
    evidence_json: str
    checked_at: datetime
    expires_at: datetime | None
    content_version_binding: str | None
