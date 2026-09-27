from typing import Protocol

from libs.delivery.dtos.email_subscription import EmailSubscription


class ConfigureEmailSubscriptionPort(Protocol):
    def __call__(
        self,
        reader_id: str,
        recipient_ref: str,
        timezone: str,
        local_time: str,
        *,
        max_items: int = 5,
    ) -> EmailSubscription: ...
