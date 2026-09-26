from collections.abc import Callable
from datetime import datetime, timezone

from libs.retrieval.dtos.active_index import (
    ActivateIndexInput,
    ActiveIndexPin,
)
from libs.retrieval.ports.active_index_store_port import ActiveIndexStorePort


class ActivateIndexGeneration:
    def __init__(
        self,
        store: ActiveIndexStorePort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self._clock = clock

    def __call__(self, value: ActivateIndexInput) -> ActiveIndexPin:
        return self._store.activate(
            value,
            activated_at=self._clock(),
        )
