from libs.watch_profiles.dtos.profile_revision import ProfileRevision
from libs.watch_profiles.ports.watch_profile_store_port import WatchProfileStorePort


class ReadWatchProfile:
    def __init__(self, store: WatchProfileStorePort) -> None:
        self._store = store

    def __call__(self, profile_id: str, revision: int | None = None) -> ProfileRevision:
        return self._store.read(profile_id, revision)
