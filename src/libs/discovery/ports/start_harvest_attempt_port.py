from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.source_page_request import SourcePageRequest


class StartHarvestAttemptPort(Protocol):
    def __call__(self, plan: CompiledSourceQuery, request: SourcePageRequest, attempt_id: str, started_at: datetime) -> HarvestAttempt: ...
