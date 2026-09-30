from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.harvest_page_result import HarvestPageResult
from libs.discovery.dtos.harvest_processing_snapshot import HarvestProcessingSnapshot
from libs.discovery.dtos.parsed_arxiv_page import ParsedArxivPage


class HarvestProcessingStorePort(Protocol):
    def snapshot(self, attempt: HarvestAttempt, parser_version: str) -> HarvestProcessingSnapshot: ...

    def commit(
        self,
        snapshot: HarvestProcessingSnapshot,
        page: ParsedArxivPage | None,
        error_code: str | None,
        processed_at: datetime,
    ) -> HarvestPageResult: ...
