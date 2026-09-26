from datetime import datetime
from typing import Protocol

from libs.retrieval.dtos.index_generation import (
    IndexGenerationSnapshot,
    PersistedIndexGeneration,
    PreparedIndexGeneration,
    PreparedIndexManifest,
)


class IndexGenerationStorePort(Protocol):
    def snapshot(self, space_id: str) -> IndexGenerationSnapshot: ...

    def start(
        self,
        generation: PreparedIndexGeneration,
        *,
        created_at: datetime,
    ) -> PersistedIndexGeneration: ...

    def mark_ready(
        self,
        generation: PreparedIndexGeneration,
        manifest: PreparedIndexManifest,
        *,
        index_sha256: str,
        verified_at: datetime,
    ) -> PersistedIndexGeneration: ...

    def mark_failed(
        self,
        generation: PreparedIndexGeneration,
        *,
        failed_at: datetime,
    ) -> PersistedIndexGeneration: ...
