from libs.watch_profiles.dtos.domain_definition import DomainDefinition
from libs.watch_profiles.ports.watch_profile_store_port import WatchProfileStorePort


class ReadDomainDefinition:
    def __init__(self, store: WatchProfileStorePort) -> None:
        self._store = store

    def __call__(self, domain_id: str, revision: int) -> DomainDefinition:
        return self._store.read_domain(domain_id, revision)
