from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.source_page_request import SourcePageRequest


class HarvestStorePort(Protocol):
    def start(
        self, plan: CompiledSourceQuery, request: SourcePageRequest, attempt_id: str, started_at: datetime
    ) -> HarvestAttempt: ...

    def read(self, attempt_id: str) -> HarvestAttempt: ...

    def record(self, attempt_id: str, capture_json: str, recorded_at: datetime) -> HarvestAttempt: ...
