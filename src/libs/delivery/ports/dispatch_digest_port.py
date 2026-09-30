from datetime import datetime
from typing import Protocol

from libs.delivery.dtos.delivery_dispatch import DispatchOutcome


class DispatchDigestPort(Protocol):
    def __call__(
        self,
        outbox_id: str,
        *,
        now: datetime,
    ) -> DispatchOutcome: ...
