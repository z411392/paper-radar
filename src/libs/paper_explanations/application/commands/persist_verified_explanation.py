from libs.paper_explanations.domain.services.explanation_persistence_rules import (
    ExplanationPersistenceRules,
)
from libs.paper_explanations.dtos.explanation_persistence import (
    PersistedExplanation,
    PersistExplanationRequest,
)
from libs.paper_explanations.ports.explanation_artifact_store_port import (
    ExplanationArtifactStorePort,
)
from libs.paper_explanations.ports.verified_explanation_store_port import (
    VerifiedExplanationStorePort,
)


class PersistVerifiedExplanation:
    def __init__(
        self,
        artifacts: ExplanationArtifactStorePort,
        store: VerifiedExplanationStorePort,
    ) -> None:
        self._artifacts = artifacts
        self._store = store

    def __call__(self, request: PersistExplanationRequest) -> PersistedExplanation:
        prepared = ExplanationPersistenceRules.prepare(request)
        self._store.validate(request)

        summary_object_id = self._artifacts.publish(prepared.summary_json)
        deterministic_report_object_id = self._artifacts.publish(
            prepared.deterministic_report_json
        )
        support_report_object_id = None
        if prepared.support_report_json is not None and prepared.support_sql_verdict is not None:
            support_report_object_id = self._artifacts.publish(prepared.support_report_json)

        return self._store.save(
            request,
            prepared,
            summary_object_id=summary_object_id,
            deterministic_report_object_id=deterministic_report_object_id,
            support_report_object_id=support_report_object_id,
        )
