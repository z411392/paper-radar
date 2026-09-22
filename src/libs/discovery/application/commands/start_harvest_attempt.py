from datetime import datetime

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.ports.harvest_store_port import HarvestStorePort


class StartHarvestAttempt:
    def __init__(self, store: HarvestStorePort) -> None:
        self._store = store

    def __call__(self, plan: CompiledSourceQuery, request: SourcePageRequest, attempt_id: str, started_at: datetime) -> HarvestAttempt:
        return self._store.start(plan, request, attempt_id, started_at)
