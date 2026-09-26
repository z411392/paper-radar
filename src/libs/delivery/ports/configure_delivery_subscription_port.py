from typing import Protocol

from libs.delivery.dtos.delivery_subscription import (
    ConfigureDeliverySubscriptionRequest,
    DeliverySubscription,
)


class ConfigureDeliverySubscriptionPort(Protocol):
    def __call__(
        self,
        request: ConfigureDeliverySubscriptionRequest,
    ) -> DeliverySubscription: ...
