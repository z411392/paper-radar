from typing import Protocol

from libs.paper_explanations.dtos.explanation_persistence import (
    PersistedExplanation,
    PersistExplanationRequest,
)


class PersistVerifiedExplanationPort(Protocol):
    def __call__(self, request: PersistExplanationRequest) -> PersistedExplanation: ...
