from typing import Protocol

from libs.retrieval.dtos.active_index import ActiveIndexPin


class SearchQueryEmbeddingPort(Protocol):
    def embed(
        self,
        pin: ActiveIndexPin,
        text: str,
    ) -> tuple[float, ...]: ...
