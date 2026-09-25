from libs.delivery.dtos.delivery_subscription import DeliverySubscription
from libs.delivery.ports.delivery_subscription_store_port import (
    DeliverySubscriptionStorePort,
)


class ReadDeliverySubscription:
    def __init__(self, store: DeliverySubscriptionStorePort) -> None:
        self._store = store

    def __call__(self, reader_id: str) -> DeliverySubscription | None:
        return self._store.read(reader_id)
