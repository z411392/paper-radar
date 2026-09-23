from datetime import datetime
from typing import Protocol

from libs.delivery.dtos.delivery_dispatch import (
    DeliveryClaim,
    DeliveryDispatchCandidate,
    MailSendResult,
)


class DeliveryDispatchStorePort(Protocol):
    def load_dispatch(self, outbox_id: str) -> DeliveryDispatchCandidate: ...

    def claim_dispatch(self, outbox_id: str, now: datetime) -> DeliveryClaim: ...

    def finish_dispatch(
        self,
        attempt_id: str,
        result: MailSendResult,
        finished_at: datetime,
    ) -> str: ...
