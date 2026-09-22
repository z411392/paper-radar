from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.harvest_page_result import HarvestPageResult


class ProcessHarvestPagePort(Protocol):
    def __call__(
        self, attempt_id: str, expected_checkpoint_version: int, processed_at: datetime
    ) -> HarvestPageResult: ...
