from dataclasses import dataclass
from datetime import datetime

from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.paper_explanations.dtos.explanation_verification import ExplanationVerificationResult
from libs.paper_explanations.dtos.reading_card_draft import ReadingCardDraft


@dataclass(frozen=True)
class PersistExplanationRequest:
    draft: ReadingCardDraft
    claims: ClaimExtractionResult
    verification: ExplanationVerificationResult
    claim_generation_run_id: str
    claim_generation_fingerprint: str
    reading_generation_run_id: str
    reading_generation_fingerprint: str
    support_generation_run_id: str | None
    support_generation_fingerprint: str | None
    created_at: datetime


@dataclass(frozen=True)
class PreparedExplanation:
    summary_id: str
    qa_state: str
    language: str
    explanation_profile: str
    summary_json: bytes
    deterministic_report_json: bytes
    support_report_json: bytes | None
    support_sql_verdict: str | None


@dataclass(frozen=True)
class PersistedExplanation:
    summary_id: str
    snapshot_id: str
    revision_id: str
    work_id: str
    generation_fingerprint: str
    generation_run_id: str
    output_object_id: str
    qa_state: str
    language: str
    explanation_profile: str
    claims_persisted: bool
