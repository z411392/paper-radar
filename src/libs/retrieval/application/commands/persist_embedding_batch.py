from collections.abc import Callable
from datetime import datetime, timezone

from libs.retrieval.domain.services.embedding_batch_rules import EmbeddingBatchRules
from libs.retrieval.dtos.embedding_batch import (
    EmbeddingBatchInput,
    PersistedEmbeddingBatch,
)
from libs.retrieval.exceptions.embedding_batch_error import EmbeddingBatchError
from libs.retrieval.ports.embedding_batch_artifact_store_port import (
    EmbeddingBatchArtifactStorePort,
)
from libs.retrieval.ports.embedding_space_store_port import EmbeddingSpaceStorePort
from libs.retrieval.ports.embedding_store_port import EmbeddingStorePort


class PersistEmbeddingBatch:
    def __init__(
        self,
        spaces: EmbeddingSpaceStorePort,
        artifacts: EmbeddingBatchArtifactStorePort,
        store: EmbeddingStorePort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._spaces = spaces
        self._artifacts = artifacts
        self._store = store
        self._clock = clock

    def __call__(self, value: EmbeddingBatchInput) -> PersistedEmbeddingBatch:
        if not isinstance(value, EmbeddingBatchInput):
            raise EmbeddingBatchError("invalid_embedding_batch")
        space = self._spaces.get(value.space_id)
        prepared = EmbeddingBatchRules.prepare(space, value)
        created_at = EmbeddingBatchRules.instant(self._clock())
        self._store.validate(prepared)
        object_id = self._artifacts.publish(prepared.content_bytes)
        if object_id != prepared.object_id:
            raise EmbeddingBatchError("embedding_artifact_mismatch")
        return self._store.save(prepared, created_at=created_at)
