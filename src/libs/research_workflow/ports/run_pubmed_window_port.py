from typing import Protocol

from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.research_workflow.dtos.pubmed_window_run_result import PubmedWindowRunResult


class RunPubmedWindowPort(Protocol):
    def __call__(
        self,
        business_key: str,
        query: SourceQueryInput,
        *,
        max_pages: int = 10,
    ) -> PubmedWindowRunResult: ...
