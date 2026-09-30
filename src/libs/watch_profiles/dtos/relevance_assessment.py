from dataclasses import dataclass


@dataclass(frozen=True)
class RelevanceRequest:
    profile_id: str
    profile_revision: int
    profile_fingerprint: str
    domain_id: str
    domain_revision: int
    snapshot_id: str
    model_name: str
    schema_version: str
    system_prompt: str
    payload_json: str
    response_schema_json: str
    input_fingerprint: str


@dataclass(frozen=True)
class RelevanceCandidate:
    model_name: str
    finish_reason: str
    content_json: str


@dataclass(frozen=True)
class RelevanceAssessment:
    input_fingerprint: str
    profile_id: str
    profile_revision: int
    domain_id: str
    domain_revision: int
    snapshot_id: str
    execution_state: str
    decision: str | None
    recommendation_reason: str | None
    anchor_ids: tuple[str, ...]
    error_code: str | None
