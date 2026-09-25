from datetime import datetime
from typing import Protocol

from libs.retrieval.dtos.index_generation import (
    IndexGenerationSnapshot,
    PersistedIndexGeneration,
    PreparedIndexGeneration,
)


class IndexGenerationStorePort(Protocol):
    def snapshot(self, space_id: str) -> IndexGenerationSnapshot: ...

    def start(
        self,
        generation: PreparedIndexGeneration,
        *,
        created_at: datetime,
    ) -> PersistedIndexGeneration: ...
