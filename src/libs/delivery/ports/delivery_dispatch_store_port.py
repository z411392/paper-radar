from datetime import datetime
from typing import Protocol

from libs.delivery.dtos.delivery_dispatch import (
    DeliveryClaim,
    DeliveryDispatchCandidate,
    DeliveryPreflightSnapshot,
    MailSendResult,
)


class DeliveryDispatchStorePort(Protocol):
    def load_dispatch(self, outbox_id: str) -> DeliveryDispatchCandidate: ...

    def load_preflight(self, outbox_id: str) -> DeliveryPreflightSnapshot: ...

    def cancel_pending(self, outbox_id: str) -> str: ...

    def claim_dispatch(self, outbox_id: str, now: datetime) -> DeliveryClaim: ...

    def finish_dispatch(
        self,
        attempt_id: str,
        result: MailSendResult,
        finished_at: datetime,
    ) -> str: ...
