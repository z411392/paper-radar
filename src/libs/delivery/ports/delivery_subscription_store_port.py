from datetime import datetime
from typing import Protocol

from libs.delivery.dtos.delivery_subscription import (
    ConfigureDeliverySubscriptionRequest,
    DeliverySubscription,
)


class DeliverySubscriptionStorePort(Protocol):
    def configure(
        self,
        request: ConfigureDeliverySubscriptionRequest,
        *,
        now: datetime,
    ) -> DeliverySubscription: ...

    def read(self, reader_id: str) -> DeliverySubscription | None: ...
