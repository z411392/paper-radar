from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryHistoryItem:
    event_id: str
    work_id: str
    title: str
    event_kind: str
    notification_state: str
    send_status: str


@dataclass(frozen=True)
class DeliveryHistory:
    digest_id: str
    outbox_id: str
    period_key: str
    delivery_state: str
    attempt_count: int
    latest_error_code: str | None
    items: tuple[DeliveryHistoryItem, ...]
