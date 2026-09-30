from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.ports.harvest_store_port import HarvestStorePort


class ReadHarvestAttempt:
    def __init__(self, store: HarvestStorePort) -> None:
        self._store = store

    def __call__(self, attempt_id: str) -> HarvestAttempt:
        return self._store.read(attempt_id)
