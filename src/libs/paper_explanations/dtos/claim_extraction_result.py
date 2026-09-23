from dataclasses import dataclass

from libs.paper_explanations.dtos.claim_extraction_request import ClaimExtractionRequest


@dataclass(frozen=True)
class PaperClaim:
    claim_id: str
    claim_type: str
    anchor_ids: tuple[str, ...]
    source_quotes: tuple[str, ...]


@dataclass(frozen=True)
class ClaimExtractionResult:
    request: ClaimExtractionRequest
    run_id: str
    claims: tuple[PaperClaim, ...]
    not_reported_in_read_evidence: tuple[str, ...]
    validation_state: str = "anchor_bound_candidate"
