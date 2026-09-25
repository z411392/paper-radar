from libs.paper_explanations.dtos.current_summary_pointer import (
    CurrentSummaryPointer,
)
from libs.paper_explanations.dtos.explanation_persistence import (
    PersistedExplanation,
)
from libs.paper_explanations.exceptions.explanation_verification_error import (
    ExplanationVerificationError,
)
from libs.paper_explanations.ports.current_summary_store_port import (
    CurrentSummaryStorePort,
)


class PublishVerifiedCurrentSummary:
    def __init__(self, store: CurrentSummaryStorePort) -> None:
        self._store = store

    def __call__(
        self,
        explanation: PersistedExplanation,
    ) -> CurrentSummaryPointer:
        if (
            not isinstance(explanation, PersistedExplanation)
            or explanation.qa_state != "passed"
        ):
            raise ExplanationVerificationError("summary_not_verified")
        current = self._store.read(
            explanation.work_id,
            explanation.language,
            explanation.explanation_profile,
        )
        expected_version = None if current is None else current.pointer_version
        return self._store.publish(
            explanation.summary_id,
            explanation.generation_fingerprint,
            expected_version,
        )
