from typing import Protocol

from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.search_hybrid import SearchSemanticBatch


class SearchSemanticIndexPort(Protocol):
    def search(
        self,
        pin: ActiveIndexPin,
        query_vector: tuple[float, ...],
        *,
        maximum_candidates: int,
    ) -> SearchSemanticBatch: ...
