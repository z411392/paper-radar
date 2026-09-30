from datetime import datetime
from typing import Protocol

from libs.delivery.dtos.scheduled_digest import ScheduledDigestOutcome, ScheduledDigestRequest


class PrepareScheduledDigestPort(Protocol):
    def __call__(
        self,
        request: ScheduledDigestRequest,
        *,
        created_at: datetime,
    ) -> ScheduledDigestOutcome: ...
