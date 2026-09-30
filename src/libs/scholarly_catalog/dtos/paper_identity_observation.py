from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PaperIdentityObservation:
    source_observation_id: str
    identifier_namespace: str
    identifier_value: str
    title: str
    content_fingerprint: str
    manifestation_kind: str
    landing_url: str
    publication_status: str
    observed_at: datetime
    source_updated_at: datetime | None = None
    published_at: datetime | None = None
