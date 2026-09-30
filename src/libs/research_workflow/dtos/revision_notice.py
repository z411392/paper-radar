from dataclasses import dataclass

from libs.delivery.dtos.scheduled_digest import ScheduledDigestRequest


@dataclass(frozen=True)
class RevisionNoticeRequest:
    outbox_id: str
    rebuild_request: ScheduledDigestRequest | None = None


@dataclass(frozen=True)
class RevisionNoticeOutcome:
    state: str
    error_code: str | None = None
