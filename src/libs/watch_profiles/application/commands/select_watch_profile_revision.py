from libs.watch_profiles.dtos.profile_revision import ProfileRevision
from libs.watch_profiles.ports.watch_profile_store_port import WatchProfileStorePort


class SelectWatchProfileRevision:
    def __init__(self, store: WatchProfileStorePort) -> None:
        self._store = store

    def __call__(
        self,
        profile_id: str,
        revision: int,
        *,
        expected_current_revision: int,
    ) -> ProfileRevision:
        return self._store.select_current_revision(
            profile_id,
            revision,
            expected_current_revision=expected_current_revision,
        )
