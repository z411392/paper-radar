from typing import Protocol

from libs.watch_profiles.dtos.profile_revision import ProfileRevision


class SelectWatchProfileRevisionPort(Protocol):
    def __call__(
        self,
        profile_id: str,
        revision: int,
        *,
        expected_current_revision: int,
    ) -> ProfileRevision: ...
