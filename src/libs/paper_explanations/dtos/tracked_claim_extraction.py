from dataclasses import dataclass

from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult


@dataclass(frozen=True)
class TrackedClaimExtraction:
    claims: ClaimExtractionResult
    run_id: str
    generation_fingerprint: str
