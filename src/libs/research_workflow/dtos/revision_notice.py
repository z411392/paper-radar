from dataclasses import dataclass


@dataclass(frozen=True)
class RevisionNoticeOutcome:
    state: str
    error_code: str | None = None
