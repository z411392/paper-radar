from datetime import datetime
from typing import Protocol

from libs.retrieval.dtos.active_index import (
    ActivateIndexInput,
    ActiveIndexPin,
)


class ActiveIndexStorePort(Protocol):
    def activate(
        self,
        value: ActivateIndexInput,
        *,
        activated_at: datetime,
    ) -> ActiveIndexPin: ...

    def pin(self, space_id: str) -> ActiveIndexPin | None: ...
