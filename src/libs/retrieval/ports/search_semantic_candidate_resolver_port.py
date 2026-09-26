from typing import Protocol

from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.search_hybrid import (
    SearchResolvedSemanticHit,
    SearchSemanticHit,
)


class SearchSemanticCandidateResolverPort(Protocol):
    def resolve(
        self,
        pin: ActiveIndexPin,
        hits: tuple[SearchSemanticHit, ...],
    ) -> tuple[SearchResolvedSemanticHit, ...]: ...
