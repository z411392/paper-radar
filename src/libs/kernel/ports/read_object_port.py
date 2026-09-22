from typing import Protocol


class ReadObjectPort(Protocol):
    def __call__(self, object_id: str) -> bytes: ...
