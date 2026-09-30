from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedRelevanceAssessment:
    assessment_id: str
    reason_json: str


@dataclass(frozen=True)
class PersistedRelevanceAssessment:
    assessment_id: str
    execution_state: str
    decision: str | None
    replayed: bool
