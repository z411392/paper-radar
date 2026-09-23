from dataclasses import dataclass


@dataclass(frozen=True)
class ClaimCandidate:
    run_id: str
    model_name: str
    finish_reason: str
    content_json: str
