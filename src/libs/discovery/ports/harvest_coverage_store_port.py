from typing import Protocol

from libs.discovery.dtos.harvest_coverage import HarvestCoverageWindow


class HarvestCoverageStorePort(Protocol):
    def read(self) -> tuple[HarvestCoverageWindow, ...]: ...


class ReadHarvestCoveragePort(Protocol):
    def __call__(self) -> tuple[HarvestCoverageWindow, ...]: ...
