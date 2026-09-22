from typing import Protocol

from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.research_workflow.dtos.harvest_run_result import HarvestRunResult


class RunHarvestSlicePort(Protocol):
    def __call__(self, query: SourceQueryInput, *, max_pages: int = 10,
                 retry_failed: bool = False) -> HarvestRunResult: ...
