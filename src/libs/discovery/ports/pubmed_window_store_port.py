from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.pubmed_search_page import PubmedSearchPage
from libs.discovery.dtos.pubmed_window import (
    PreparedPubmedBibliographyObservation,
    PubmedWindowProgress,
)


class PubmedWindowStorePort(Protocol):
    def ensure(
        self,
        business_key: str,
        plan: CompiledSourceQuery,
        *,
        created_at: datetime,
    ) -> PubmedWindowProgress: ...

    def read(self, business_key: str) -> PubmedWindowProgress: ...

    def record_search_page(
        self,
        business_key: str,
        page: PubmedSearchPage,
        *,
        raw_object_id: str,
        observed_at: datetime,
    ) -> PubmedWindowProgress: ...

    def commit_bibliography(
        self,
        business_key: str,
        observations: tuple[PreparedPubmedBibliographyObservation, ...],
    ) -> PubmedWindowProgress: ...
