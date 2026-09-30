from typing import Protocol

from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment


class AssessPaperRelevancePort(Protocol):
    def __call__(
        self, profile_id: str, domain_id: str, claims: ClaimExtractionResult
    ) -> RelevanceAssessment: ...
