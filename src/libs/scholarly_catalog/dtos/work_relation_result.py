from dataclasses import dataclass


@dataclass(frozen=True)
class WorkRelationResult:
    relation_id: str
    source_work_id: str
    target_work_id: str
    relation_type: str
    evidence_json: str
    observed_at: str
    created: bool
