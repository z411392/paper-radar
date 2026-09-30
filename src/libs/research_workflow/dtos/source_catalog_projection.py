from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceCatalogProjectionProgress:
    unit_id: str
    source: str
    last_observation_id: str | None
    projected_count: int
    state: str
    checkpoint_version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SourceCatalogProjectionResult:
    unit_id: str
    source: str
    state: str
    projected_count: int
    processed_in_call: int
    last_observation_id: str | None
