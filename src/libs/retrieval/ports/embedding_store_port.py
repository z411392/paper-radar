from datetime import datetime
from typing import Protocol

from libs.retrieval.dtos.embedding_batch import (
    PersistedEmbeddingBatch,
    PreparedEmbeddingBatch,
)


class EmbeddingStorePort(Protocol):
    def validate(self, batch: PreparedEmbeddingBatch) -> None: ...

    def save(
        self,
        batch: PreparedEmbeddingBatch,
        *,
        created_at: datetime,
    ) -> PersistedEmbeddingBatch: ...
