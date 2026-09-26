from typing import Protocol

from libs.retrieval.dtos.index_build import IndexBuildRequest
from libs.retrieval.dtos.index_generation import PreparedIndexGeneration


class IndexVectorLoaderPort(Protocol):
    def load(
        self,
        generation: PreparedIndexGeneration,
    ) -> IndexBuildRequest: ...
