from typing import Protocol

from libs.retrieval.dtos.index_build import BuiltIndex, IndexBuildRequest


class IndexBuilderPort(Protocol):
    def build(self, request: IndexBuildRequest) -> BuiltIndex: ...
