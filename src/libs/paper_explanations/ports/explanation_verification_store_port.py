from datetime import datetime
from typing import Protocol

from libs.paper_explanations.dtos.explanation_verification import ExplanationVerificationResult
from libs.paper_explanations.dtos.verification_persistence_result import VerificationPersistenceResult


class ExplanationVerificationStorePort(Protocol):
    def record(
        self,
        summary_id: str,
        result: ExplanationVerificationResult,
        report_object_id: str,
        verified_at: datetime,
    ) -> VerificationPersistenceResult: ...
