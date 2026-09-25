from datetime import datetime
from typing import Protocol

from libs.delivery.dtos.delivery_queue import QueuedDigest
from libs.delivery.dtos.digest_preview import DigestPreview


class QueueDigestPort(Protocol):
    def __call__(
        self,
        preview: DigestPreview,
        *,
        reader_id: str,
        channel: str,
        workspace_epoch: int,
        created_at: datetime,
        rebuild_reason: str | None = None,
    ) -> QueuedDigest: ...
