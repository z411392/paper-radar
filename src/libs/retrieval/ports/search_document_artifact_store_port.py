from typing import Protocol


class SearchDocumentArtifactStorePort(Protocol):
    def publish(self, content: bytes) -> str: ...
