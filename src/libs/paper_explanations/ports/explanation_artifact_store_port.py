from typing import Protocol


class ExplanationArtifactStorePort(Protocol):
    def publish(self, content: bytes) -> str: ...
