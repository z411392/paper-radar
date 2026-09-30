from dataclasses import dataclass


@dataclass(frozen=True)
class CrossrefRecoveredAttempt:
    receipt_id: str
    action: str
    failure_code: str | None
