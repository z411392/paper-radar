from typing import Protocol


class GenerationOutputStorePort(Protocol):
    def publish(self, content: bytes) -> str: ...

    def read(self, object_id: str) -> bytes: ...
