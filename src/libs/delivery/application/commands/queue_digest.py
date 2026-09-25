from datetime import datetime

from libs.delivery.dtos.delivery_queue import QueueDigestRequest, QueuedDigest
from libs.delivery.dtos.digest_preview import DigestPreview
from libs.delivery.ports.delivery_store_port import DeliveryStorePort
from libs.delivery.ports.digest_artifact_store_port import DigestArtifactStorePort


class QueueDigest:
    def __init__(
        self,
        artifacts: DigestArtifactStorePort,
        store: DeliveryStorePort,
    ) -> None:
        self._artifacts = artifacts
        self._store = store

    def __call__(
        self,
        preview: DigestPreview,
        *,
        reader_id: str,
        channel: str,
        workspace_epoch: int,
        created_at: datetime,
        rebuild_reason: str | None = None,
        rebuild_outbox_id: str | None = None,
    ) -> QueuedDigest:
        rendered_object_id = self._artifacts.publish(preview)
        return self._store.queue(
            QueueDigestRequest(
                preview=preview,
                reader_id=reader_id,
                channel=channel,
                rendered_object_id=rendered_object_id,
                workspace_epoch=workspace_epoch,
                created_at=created_at,
                rebuild_reason=rebuild_reason,
                rebuild_outbox_id=rebuild_outbox_id,
            )
        )
