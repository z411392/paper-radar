from typing import Protocol

from libs.paper_explanations.dtos.explanation_persistence import (
    PersistedExplanation,
    PersistExplanationRequest,
    PreparedExplanation,
)


class VerifiedExplanationStorePort(Protocol):
    def validate(self, request: PersistExplanationRequest) -> None: ...

    def save(
        self,
        request: PersistExplanationRequest,
        prepared: PreparedExplanation,
        *,
        summary_object_id: str,
        deterministic_report_object_id: str,
        support_report_object_id: str | None,
    ) -> PersistedExplanation: ...
