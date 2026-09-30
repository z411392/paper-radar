from dataclasses import dataclass


@dataclass(frozen=True)
class CrossrefRateDecision:
    """Operational advice only; accept does not mean parsed, durable or collected."""

    action: str
    failure_code: str | None
    delay_seconds: float
    minimum_interval_seconds: float
    reported_concurrency: int | None
    rate_type: str | None
    warnings: tuple[str, ...]
    open_circuit: bool = False
