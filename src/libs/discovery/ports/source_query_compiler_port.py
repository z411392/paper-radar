from typing import Protocol

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.source_capabilities import SourceCapabilities
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.dtos.source_query_input import SourceQueryInput


class SourceQueryCompilerPort(Protocol):
    def describe(self) -> SourceCapabilities: ...

    def compile(self, query: SourceQueryInput) -> CompiledSourceQuery: ...

    def page(self, plan: CompiledSourceQuery, start: int = 0) -> SourcePageRequest: ...
