from typing import Protocol

from libs.paper_explanations.dtos.claim_extraction_result import (
    ClaimExtractionResult,
)
from libs.paper_explanations.dtos.reading_card_draft import ReadingCardDraft
from libs.paper_explanations.dtos.tracked_explanation_verification import (
    TrackedExplanationVerification,
)


class VerifyTrackedExplanationPort(Protocol):
    def __call__(
        self,
        draft: ReadingCardDraft,
        claims: ClaimExtractionResult,
    ) -> TrackedExplanationVerification: ...
