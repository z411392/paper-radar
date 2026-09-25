from dataclasses import dataclass

from libs.scholarly_catalog.dtos.local_paper_record import LocalPaperRecord


@dataclass(frozen=True)
class LocalDeliveryAttemptView:
    attempt_id: str
    attempt_no: int
    state: str
    provider_message_id: str | None
    error_code: str | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class LocalNotificationView:
    ledger_id: str
    event_id: str
    event_kind: str
    work_id: str
    channel: str
    ledger_state: str
    ledger_created_at: str
    digest_id: str
    period_key: str
    item_kind: str
    summary_id: str | None
    revision_id: str | None
    outbox_id: str
    outbox_state: str
    attempts: tuple[LocalDeliveryAttemptView, ...]


@dataclass(frozen=True)
class LocalReadingHistory:
    paper: LocalPaperRecord
    notifications: tuple[LocalNotificationView, ...]
