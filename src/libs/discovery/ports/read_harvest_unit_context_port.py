from typing import Protocol

from libs.discovery.dtos.harvest_unit_context import HarvestUnitContext


class ReadHarvestUnitContextPort(Protocol):
    def __call__(self, unit_id: str, source: str) -> HarvestUnitContext: ...
