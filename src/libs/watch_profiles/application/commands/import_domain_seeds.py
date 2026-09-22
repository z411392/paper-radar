from libs.watch_profiles.domain.services.normalize_watch_configuration import NormalizeWatchConfiguration
from libs.watch_profiles.dtos.domain_seed_outcome import DomainSeedOutcome
from libs.watch_profiles.ports.watch_profile_store_port import WatchProfileStorePort


class ImportDomainSeeds:
    def __init__(self, normalize: NormalizeWatchConfiguration, store: WatchProfileStorePort) -> None:
        self._normalize = normalize
        self._store = store

    def __call__(self, payload: str) -> tuple[DomainSeedOutcome, ...]:
        document = self._normalize("domains", payload)
        return self._store.import_domains(document)
