from typing import Protocol

from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.source_query_input import SourceQueryInput


class CompileSourceQueryPort(Protocol):
    def __call__(self, query: SourceQueryInput) -> CompiledSourceQuery: ...
