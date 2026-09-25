from libs.paper_explanations.dtos.explanation_persistence import (
    PersistExplanationRequest,
)
from libs.paper_explanations.exceptions.claim_extraction_error import (
    ClaimExtractionError,
)
from libs.paper_explanations.exceptions.explanation_verification_error import (
    ExplanationVerificationError,
)
from libs.paper_explanations.exceptions.generation_ledger_error import (
    GenerationLedgerError,
)
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.exceptions.reading_card_error import ReadingCardError
from libs.paper_explanations.ports.extract_tracked_paper_claims_port import (
    ExtractTrackedPaperClaimsPort,
)
from libs.paper_explanations.ports.generate_tracked_reading_card_port import (
    GenerateTrackedReadingCardPort,
)
from libs.paper_explanations.ports.persist_verified_explanation_port import (
    PersistVerifiedExplanationPort,
)
from libs.paper_explanations.ports.publish_verified_current_summary_port import (
    PublishVerifiedCurrentSummaryPort,
)
from libs.paper_explanations.ports.verify_tracked_explanation_port import (
    VerifyTrackedExplanationPort,
)
from libs.research_workflow.dtos.evidence_explanation import (
    EvidenceExplanationRequest,
    EvidenceExplanationResult,
)
from libs.research_workflow.ports.workflow_clock_port import WorkflowClockPort
from libs.watch_profiles.exceptions.relevance_assessment_error import (
    RelevanceAssessmentError,
)
from libs.watch_profiles.exceptions.relevance_persistence_error import (
    RelevancePersistenceError,
)
from libs.watch_profiles.ports.assess_tracked_paper_relevance_port import (
    AssessTrackedPaperRelevancePort,
)
from libs.watch_profiles.ports.persist_relevance_assessment_port import (
    PersistRelevanceAssessmentPort,
)


class ProcessEvidenceExplanation:
    _AWAITING_CODES = frozenset(
        {
            "model_disabled",
            "budget_blocked",
            "authentication_failed",
            "permission_denied",
            "endpoint_unavailable",
            "generation_ledger_corrupt",
        }
    )

    def __init__(
        self,
        claims: ExtractTrackedPaperClaimsPort,
        relevance: AssessTrackedPaperRelevancePort,
        persist_relevance: PersistRelevanceAssessmentPort,
        reading: GenerateTrackedReadingCardPort,
        verification: VerifyTrackedExplanationPort,
        persist_explanation: PersistVerifiedExplanationPort,
        publish_current: PublishVerifiedCurrentSummaryPort,
        clock: WorkflowClockPort,
    ) -> None:
        self._claims = claims
        self._relevance = relevance
        self._persist_relevance = persist_relevance
        self._reading = reading
        self._verification = verification
        self._persist_explanation = persist_explanation
        self._publish_current = publish_current
        self._clock = clock

    @classmethod
    def _failure(cls, code: str) -> EvidenceExplanationResult:
        state = (
            "awaiting_external"
            if code in cls._AWAITING_CODES
            or any(token in code for token in ("corrupt", "missing", "mismatch"))
            else "failed"
        )
        return EvidenceExplanationResult(state, error_code=code)

    def __call__(
        self,
        request: EvidenceExplanationRequest,
    ) -> EvidenceExplanationResult:
        if not isinstance(request, EvidenceExplanationRequest):
            return EvidenceExplanationResult(
                "failed",
                error_code="invalid_explanation_request",
            )
        try:
            tracked_claims = self._claims(request.snapshot_id)
            claims = tracked_claims.claims
            claim_request = claims.request
            if (
                claim_request.snapshot_id != request.snapshot_id
                or claim_request.revision_id != request.revision_id
                or claim_request.work_id != request.work_id
            ):
                return EvidenceExplanationResult(
                    "awaiting_external",
                    error_code="explanation_evidence_mismatch",
                )

            tracked_relevance = self._relevance(
                request.profile_id,
                request.domain_id,
                claims,
            )
            assessment = tracked_relevance.assessment
            if (
                assessment.snapshot_id != request.snapshot_id
                or assessment.profile_id != request.profile_id
                or assessment.domain_id != request.domain_id
            ):
                return EvidenceExplanationResult(
                    "awaiting_external",
                    error_code="explanation_relevance_mismatch",
                )
            if (
                assessment.profile_revision != request.profile_revision
                or assessment.domain_revision != request.domain_revision
                or assessment.execution_state == "stale"
            ):
                return EvidenceExplanationResult(
                    "cancelled",
                    error_code="scheduled_input_stale",
                )

            persisted_relevance = self._persist_relevance(
                assessment,
                assessed_at=self._clock.now(),
            )
            if assessment.execution_state == "failed":
                return EvidenceExplanationResult(
                    "failed",
                    relevance_assessment_id=persisted_relevance.assessment_id,
                    error_code=assessment.error_code or "relevance_assessment_failed",
                )
            if (
                assessment.execution_state != "succeeded"
                or assessment.decision
                not in {"direct", "adjacent", "uncertain", "irrelevant"}
            ):
                return EvidenceExplanationResult(
                    "awaiting_external",
                    relevance_assessment_id=persisted_relevance.assessment_id,
                    error_code="relevance_assessment_corrupt",
                )
            if assessment.decision in {"uncertain", "irrelevant"}:
                return EvidenceExplanationResult(
                    "succeeded",
                    relevance_assessment_id=persisted_relevance.assessment_id,
                )

            tracked_reading = self._reading(claims)
            tracked_verification = self._verification(
                tracked_reading.draft,
                claims,
            )
            persisted = self._persist_explanation(
                PersistExplanationRequest(
                    tracked_reading.draft,
                    claims,
                    tracked_verification.verification,
                    tracked_claims.run_id,
                    tracked_claims.generation_fingerprint,
                    tracked_reading.run_id,
                    tracked_reading.generation_fingerprint,
                    tracked_verification.support_run_id,
                    tracked_verification.support_generation_fingerprint,
                    self._clock.now(),
                )
            )
            if persisted.qa_state == "pending":
                return EvidenceExplanationResult(
                    "awaiting_external",
                    persisted.summary_id,
                    persisted_relevance.assessment_id,
                    "summary_verification_pending",
                )
            if persisted.qa_state == "rejected":
                return EvidenceExplanationResult(
                    "cancelled",
                    persisted.summary_id,
                    persisted_relevance.assessment_id,
                    "summary_verification_rejected",
                )
            if persisted.qa_state != "passed":
                return EvidenceExplanationResult(
                    "awaiting_external",
                    persisted.summary_id,
                    persisted_relevance.assessment_id,
                    "summary_qa_state_corrupt",
                )
            pointer = self._publish_current(persisted)
            if pointer.summary_id != persisted.summary_id:
                return EvidenceExplanationResult(
                    "awaiting_external",
                    persisted.summary_id,
                    persisted_relevance.assessment_id,
                    "current_summary_mismatch",
                )
            return EvidenceExplanationResult(
                "succeeded",
                persisted.summary_id,
                persisted_relevance.assessment_id,
                None,
            )
        except ExplanationVerificationError as exc:
            if exc.code == "stale_summary":
                return EvidenceExplanationResult(
                    "cancelled",
                    error_code="stale_summary",
                )
            return self._failure(exc.code)
        except (
            ClaimExtractionError,
            GenerationLedgerError,
            ModelGatewayError,
            ReadingCardError,
            RelevanceAssessmentError,
            RelevancePersistenceError,
        ) as exc:
            return self._failure(exc.code)
