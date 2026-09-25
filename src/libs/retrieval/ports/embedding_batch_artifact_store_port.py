from typing import Protocol


class EmbeddingBatchArtifactStorePort(Protocol):
    def publish(self, content: bytes) -> str: ...
