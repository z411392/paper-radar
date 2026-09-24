from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrossrefCaptureResolution:
    claim_id: str
    attempt_id: str
    receipt_id: str
    action: str
    failure_code: str | None
    resolved_at: datetime
    retry_not_before: datetime | None
    policy_version: str
    replayed: bool = False
