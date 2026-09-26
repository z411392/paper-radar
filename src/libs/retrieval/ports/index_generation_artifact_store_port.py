from typing import Protocol

from libs.retrieval.dtos.index_build import PublishedIndexArtifacts
from libs.retrieval.dtos.index_generation import (
    PreparedIndexGeneration,
    PreparedIndexManifest,
)


class IndexGenerationArtifactStorePort(Protocol):
    def publish(
        self,
        generation: PreparedIndexGeneration,
        *,
        index_bytes: bytes,
        manifest: PreparedIndexManifest,
    ) -> PublishedIndexArtifacts: ...
