from typing import Protocol

from libs.watch_profiles.dtos.profile_revision import ProfileRevision


class ReadWatchProfilePort(Protocol):
    def __call__(self, profile_id: str, revision: int | None = None) -> ProfileRevision: ...
