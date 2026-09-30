from dataclasses import dataclass


@dataclass(frozen=True)
class GenerationBudgetPolicy:
    period_key: str
    currency: str
    period_limit_micros: int
    reservation_micros: int
    execution_policy_fingerprint: str
    gateway: str = "openrouter"
    endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
