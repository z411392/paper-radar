from collections.abc import Callable
from datetime import datetime, timezone

from libs.delivery.dtos.delivery_subscription import (
    ConfigureDeliverySubscriptionRequest,
    DeliverySubscription,
)
from libs.delivery.ports.delivery_subscription_store_port import (
    DeliverySubscriptionStorePort,
)


class ConfigureDeliverySubscription:
    def __init__(
        self,
        store: DeliverySubscriptionStorePort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self._clock = clock

    def __call__(
        self,
        request: ConfigureDeliverySubscriptionRequest,
    ) -> DeliverySubscription:
        return self._store.configure(request, now=self._clock())
