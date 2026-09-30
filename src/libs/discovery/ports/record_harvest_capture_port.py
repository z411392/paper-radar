from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.source_fetch_result import SourceFetchResult


class RecordHarvestCapturePort(Protocol):
    def __call__(
        self, attempt_id: str, result: SourceFetchResult, recorded_at: datetime
    ) -> HarvestAttempt: ...
