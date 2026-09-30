from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.pubmed_bibliography_batch import PubmedBibliographyBatch
from libs.discovery.dtos.pubmed_harvest_state import PendingPubmedBatch, PubmedHarvestState
from libs.discovery.dtos.pubmed_search_page import PubmedSearchPage


class PubmedHarvestStorePort(Protocol):
    def ensure(self, plan: CompiledSourceQuery, created_at: datetime) -> PubmedHarvestState: ...

    def read(self, plan: CompiledSourceQuery) -> PubmedHarvestState: ...

    def save_search(
        self,
        plan: CompiledSourceQuery,
        page: PubmedSearchPage,
        search_object_id: str,
        observed_at: datetime,
    ) -> PubmedHarvestState: ...

    def next_batch(
        self,
        plan: CompiledSourceQuery,
        *,
        maximum_batch_size: int,
    ) -> PendingPubmedBatch | None: ...

    def save_bibliography(
        self,
        plan: CompiledSourceQuery,
        pending: PendingPubmedBatch,
        batch: PubmedBibliographyBatch,
        payload_object_id: str,
        observed_at: datetime,
    ) -> PubmedHarvestState: ...
