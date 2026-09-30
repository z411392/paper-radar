from dataclasses import dataclass
from datetime import datetime

from libs.delivery.dtos.digest_preview import DigestPreview


@dataclass(frozen=True)
class QueueDigestRequest:
    preview: DigestPreview
    reader_id: str
    channel: str
    rendered_object_id: str
    workspace_epoch: int
    created_at: datetime
    rebuild_reason: str | None = None
    rebuild_outbox_id: str | None = None


@dataclass(frozen=True)
class QueuedDigest:
    digest_id: str
    outbox_id: str
    idempotency_key: str
    payload_sha256: str
    replayed: bool
