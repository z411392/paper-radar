from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrossrefWindowSplit:
    parent_window_id: str
    left_window_id: str
    right_window_id: str
    left_from: datetime
    left_until: datetime
    right_from: datetime
    right_until: datetime
    reason: str
    created_at: datetime
    replayed: bool
