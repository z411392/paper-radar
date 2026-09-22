from libs.watch_profiles.exceptions.watch_configuration_error import WatchConfigurationError
from libs.watch_profiles.ports.watch_profile_store_port import WatchProfileStorePort


class SetWatchProfileLifecycle:
    def __init__(self, store: WatchProfileStorePort) -> None:
        self._store = store

    def __call__(self, profile_id: str, lifecycle: str) -> None:
        if lifecycle not in {"active", "paused"}:
            raise WatchConfigurationError("invalid_lifecycle")
        self._store.set_lifecycle(profile_id, lifecycle)
