from typing import Protocol

from libs.paper_explanations.dtos.claim_candidate import ClaimCandidate
from libs.paper_explanations.dtos.claim_extraction_request import ClaimExtractionRequest


class ClaimCandidatePort(Protocol):
    def __call__(self, request: ClaimExtractionRequest) -> ClaimCandidate: ...
