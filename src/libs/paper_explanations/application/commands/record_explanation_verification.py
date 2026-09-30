from datetime import datetime

from libs.paper_explanations.domain.services.verification_report_rules import VerificationReportRules
from libs.paper_explanations.dtos.explanation_verification import ExplanationVerificationResult
from libs.paper_explanations.dtos.verification_persistence_result import VerificationPersistenceResult
from libs.paper_explanations.ports.explanation_verification_store_port import ExplanationVerificationStorePort
from libs.paper_explanations.ports.verification_report_store_port import VerificationReportStorePort


class RecordExplanationVerification:
    def __init__(
        self,
        reports: VerificationReportStorePort,
        store: ExplanationVerificationStorePort,
    ) -> None:
        self._reports = reports
        self._store = store

    def __call__(
        self,
        summary_id: str,
        result: ExplanationVerificationResult,
        *,
        verified_at: datetime,
    ) -> VerificationPersistenceResult:
        content = VerificationReportRules.serialize(summary_id, result)
        report_object_id = self._reports.publish(content)
        return self._store.record(summary_id, result, report_object_id, verified_at)
