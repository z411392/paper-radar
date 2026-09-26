from datetime import datetime
from typing import Protocol

from libs.retrieval.dtos.embedding_space import (
    PreparedEmbeddingSpace,
    RegisteredEmbeddingSpace,
)


class EmbeddingSpaceStorePort(Protocol):
    def get(self, space_id: str) -> RegisteredEmbeddingSpace: ...

    def save(
        self,
        space: PreparedEmbeddingSpace,
        *,
        created_at: datetime,
    ) -> RegisteredEmbeddingSpace: ...
