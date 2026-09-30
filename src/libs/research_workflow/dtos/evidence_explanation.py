from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceExplanationRequest:
    snapshot_id: str
    revision_id: str
    work_id: str
    profile_id: str
    profile_revision: int
    domain_id: str
    domain_revision: int


@dataclass(frozen=True)
class EvidenceExplanationResult:
    state: str
    summary_id: str | None = None
    relevance_assessment_id: str | None = None
    error_code: str | None = None
