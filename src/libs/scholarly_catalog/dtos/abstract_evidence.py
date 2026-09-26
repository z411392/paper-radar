from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AbstractEvidenceRequest:
    revision_id: str
    work_id: str
    parser_version: str
    abstract: str | None
    observed_at: datetime


@dataclass(frozen=True)
class AbstractEvidenceResult:
    state: str
    revision_id: str
    work_id: str
    snapshot_id: str | None
