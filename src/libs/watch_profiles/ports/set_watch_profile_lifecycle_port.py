from typing import Protocol


class SetWatchProfileLifecyclePort(Protocol):
    def __call__(self, profile_id: str, lifecycle: str) -> None: ...
