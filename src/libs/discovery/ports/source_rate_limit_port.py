from contextlib import AbstractContextManager
from typing import Protocol


class SourceRateLimitLeasePort(Protocol):
    def defer(self, seconds: float) -> None: ...


class SourceRateLimitPort(Protocol):
    def slot(self) -> AbstractContextManager[SourceRateLimitLeasePort]: ...
