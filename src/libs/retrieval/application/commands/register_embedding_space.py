from collections.abc import Callable
from datetime import datetime, timezone

from libs.retrieval.domain.services.embedding_space_rules import EmbeddingSpaceRules
from libs.retrieval.dtos.embedding_space import (
    EmbeddingSpaceInput,
    RegisteredEmbeddingSpace,
)
from libs.retrieval.ports.embedding_space_store_port import EmbeddingSpaceStorePort


class RegisterEmbeddingSpace:
    def __init__(
        self,
        store: EmbeddingSpaceStorePort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self._clock = clock

    def __call__(self, value: EmbeddingSpaceInput) -> RegisteredEmbeddingSpace:
        prepared = EmbeddingSpaceRules.prepare(value)
        created_at = EmbeddingSpaceRules.instant(self._clock())
        return self._store.save(prepared, created_at=created_at)
