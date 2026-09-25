from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DigestResearchEvent:
    event_id: str
    work_id: str
    revision_id: str | None
    event_kind: str
    observed_at: datetime
    title: str
    source_url: str | None
