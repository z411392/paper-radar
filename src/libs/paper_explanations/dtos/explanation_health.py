from dataclasses import dataclass


@dataclass(frozen=True)
class UsagePeriodHealthEvidence:
    period_key: str
    currency: str
    reserved_micros: int
    settled_actual_micros: int
    unknown_cost_reservations: int


@dataclass(frozen=True)
class ExplanationHealthEvidence:
    qa_rejected: int
    usage_periods: tuple[UsagePeriodHealthEvidence, ...]
