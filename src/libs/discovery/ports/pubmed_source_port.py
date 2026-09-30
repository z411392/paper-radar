from typing import Protocol

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.pubmed_bibliography_batch import PubmedBibliographyBatch
from libs.discovery.dtos.pubmed_search_page import PubmedSearchPage
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.dtos.source_query_input import SourceQueryInput


class PubmedSourcePort(Protocol):
    def compile(self, query: SourceQueryInput) -> CompiledSourceQuery: ...

    def page(self, plan: CompiledSourceQuery, start: int = 0) -> SourcePageRequest: ...

    def parse_search(
        self,
        request: SourcePageRequest,
        body: bytes,
        *,
        http_status: int,
    ) -> PubmedSearchPage: ...

    def bibliography_request(self, pmids: tuple[str, ...]) -> SourcePageRequest: ...

    def parse_bibliography(
        self,
        request: SourcePageRequest,
        body: bytes,
        *,
        http_status: int,
    ) -> PubmedBibliographyBatch: ...
