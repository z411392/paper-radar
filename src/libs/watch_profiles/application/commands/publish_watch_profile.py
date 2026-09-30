from libs.watch_profiles.domain.services.normalize_watch_configuration import NormalizeWatchConfiguration
from libs.watch_profiles.dtos.profile_revision import ProfileRevision
from libs.watch_profiles.ports.watch_profile_store_port import WatchProfileStorePort


class PublishWatchProfile:
    def __init__(self, normalize: NormalizeWatchConfiguration, store: WatchProfileStorePort) -> None:
        self._normalize = normalize
        self._store = store

    def __call__(self, payload: str, *, expected_revision: int | None) -> ProfileRevision:
        document = self._normalize("profile", payload)
        return self._store.publish(document, expected_revision)
