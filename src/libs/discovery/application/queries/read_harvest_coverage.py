from libs.discovery.dtos.harvest_coverage import HarvestCoverageWindow
from libs.discovery.ports.harvest_coverage_store_port import HarvestCoverageStorePort


class ReadHarvestCoverage:
    def __init__(self, store: HarvestCoverageStorePort) -> None:
        self._store = store

    def __call__(self) -> tuple[HarvestCoverageWindow, ...]:
        return self._store.read()
