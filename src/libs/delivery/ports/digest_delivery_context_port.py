from typing import Protocol

from libs.delivery.dtos.scheduled_digest import DigestSubscriptionContext


class DigestDeliveryContextPort(Protocol):
    def load(self, subscription_id: str) -> DigestSubscriptionContext: ...

    def already_notified(
        self,
        reader_id: str,
        channel: str,
        event_ids: tuple[str, ...],
    ) -> frozenset[str]: ...
