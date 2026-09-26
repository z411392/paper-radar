from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ReaderFeedbackRequest:
    feedback_id: str
    reader_id: str
    work_id: str
    action: str
    profile_id: str | None
    created_at: datetime


@dataclass(frozen=True)
class RecordedFeedback:
    feedback_id: str
    replayed: bool
