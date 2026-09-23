from dataclasses import dataclass


@dataclass(frozen=True)
class WorkAliasResult:
    alias_work_id: str
    canonical_work_id: str
    evidence_json: str
    decided_at: str
    created: bool
