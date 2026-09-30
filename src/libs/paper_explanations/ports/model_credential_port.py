from typing import Protocol


class ModelCredentialPort(Protocol):
    def __call__(self) -> str: ...
