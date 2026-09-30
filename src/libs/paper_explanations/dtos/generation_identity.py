from dataclasses import dataclass


@dataclass(frozen=True)
class GenerationIdentity:
    generation_fingerprint: str
    request_input_fingerprint: str
    task_kind: str
    gateway: str
    endpoint: str
    requested_model: str
    prompt_digest: str
    execution_policy_fingerprint: str
    period_key: str
    currency: str
    period_limit_micros: int
    reservation_micros: int
