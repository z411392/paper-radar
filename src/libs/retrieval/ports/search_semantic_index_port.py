from typing import Protocol

from libs.retrieval.dtos.active_index_artifact import ActiveIndexArtifacts
from libs.retrieval.dtos.search_hybrid import SearchSemanticBatch


class SearchSemanticIndexPort(Protocol):
    def search(
        self,
        artifacts: ActiveIndexArtifacts,
        query_vector: tuple[float, ...],
        *,
        maximum_candidates: int,
    ) -> SearchSemanticBatch: ...
