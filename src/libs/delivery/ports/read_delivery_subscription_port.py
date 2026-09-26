from typing import Protocol

from libs.delivery.dtos.delivery_subscription import DeliverySubscription


class ReadDeliverySubscriptionPort(Protocol):
    def __call__(self, reader_id: str) -> DeliverySubscription | None: ...
