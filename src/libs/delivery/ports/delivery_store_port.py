from typing import Protocol

from libs.delivery.dtos.delivery_queue import QueueDigestRequest, QueuedDigest


class DeliveryStorePort(Protocol):
    def queue(self, request: QueueDigestRequest) -> QueuedDigest: ...
