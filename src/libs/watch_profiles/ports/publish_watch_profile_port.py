from typing import Protocol

from libs.watch_profiles.dtos.profile_revision import ProfileRevision


class PublishWatchProfilePort(Protocol):
    def __call__(self, payload: str, *, expected_revision: int | None) -> ProfileRevision: ...
