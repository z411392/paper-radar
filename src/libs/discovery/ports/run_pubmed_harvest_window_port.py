from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.pubmed_harvest_run_result import PubmedHarvestRunResult
from libs.discovery.dtos.source_query_input import SourceQueryInput


class RunPubmedHarvestWindowPort(Protocol):
    def __call__(
        self,
        query: SourceQueryInput,
        *,
        started_at: datetime,
        max_batches: int = 10,
    ) -> PubmedHarvestRunResult: ...
