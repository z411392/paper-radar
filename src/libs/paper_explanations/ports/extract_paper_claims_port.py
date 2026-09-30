from typing import Protocol

from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult


class ExtractPaperClaimsPort(Protocol):
    def __call__(self, snapshot_id: str) -> ClaimExtractionResult: ...
