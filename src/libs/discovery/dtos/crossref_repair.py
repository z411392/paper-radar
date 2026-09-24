from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrossrefRepairPolicy:
    safety_lag_seconds: int
    lookback_windows: int
    periodic_repair_after_seconds: int
    max_windows: int


@dataclass(frozen=True)
class CrossrefRepairCandidate:
    window_id: str
    reason: str
    from_index: datetime
    until_index: datetime


@dataclass(frozen=True)
class CrossrefRepairRun:
    repair_id: str
    window_id: str
    repair_no: int
    pass_id: str
    pass_no: int
    state: str
    reason: str
    error_code: str | None


@dataclass(frozen=True)
class CrossrefWindowFinalization:
    finalization_id: str
    window_id: str
    stream_id: str
    generation: int
    pass_id: str
    reason: str
    finalized_at: datetime


@dataclass(frozen=True)
class CrossrefBindingWatermark:
    stream_id: str
    binding_key: str
    config_version: str
    rows: int
    finalized_until: datetime
    latest_window_id: str
    latest_finalization_id: str
    updated_at: datetime
