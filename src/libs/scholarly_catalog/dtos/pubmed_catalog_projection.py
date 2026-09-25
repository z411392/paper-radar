from dataclasses import dataclass


@dataclass(frozen=True)
class PubmedCatalogProjection:
    observation_id: str
    work_id: str
    canonical_work_id: str
    manifestation_id: str
    revision_id: str
    event_id: str
    content_fingerprint: str
    created_work: bool
    created_manifestation: bool
    created_revision: bool
