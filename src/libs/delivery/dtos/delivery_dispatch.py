from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryDispatchCandidate:
    outbox_id: str
    digest_id: str
    subscription_id: str
    period_key: str
    rendered_object_id: str
    idempotency_key: str
    payload_sha256: str
    workspace_epoch: int
    outbox_state: str
    digest_state: str
    reader_id: str
    channel: str
    enabled: bool
    recipient_ref: str


@dataclass(frozen=True)
class DeliveryClaim:
    state: str
    attempt_id: str | None = None
    attempt_no: int | None = None


@dataclass(frozen=True)
class MailMessage:
    recipient: str
    subject: str
    text_body: str
    html_body: str
    idempotency_key: str


@dataclass(frozen=True)
class MailSendResult:
    state: str
    provider_message_id: str | None
    error_code: str | None


@dataclass(frozen=True)
class DispatchOutcome:
    state: str
    outbox_id: str
    attempt_id: str | None = None


@dataclass(frozen=True)
class ReconciliationOutcome:
    state: str
    reason: str | None = None


@dataclass(frozen=True)
class DeliveryPreflightItem:
    event_id: str
    work_id: str
    summary_id: str | None
    revision_id: str | None
    item_kind: str
    event_kind: str


@dataclass(frozen=True)
class DeliveryPreflightSnapshot:
    outbox_id: str
    digest_id: str
    subscription_id: str
    reader_id: str
    channel: str
    enabled: bool
    outbox_state: str
    digest_state: str
    items: tuple[DeliveryPreflightItem, ...]
