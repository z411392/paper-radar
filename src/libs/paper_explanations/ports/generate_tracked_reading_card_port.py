from typing import Protocol

from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.paper_explanations.dtos.tracked_reading_card import TrackedReadingCard


class GenerateTrackedReadingCardPort(Protocol):
    def __call__(
        self,
        claims: ClaimExtractionResult,
        *,
        glossary: tuple[tuple[str, str], ...] = (),
    ) -> TrackedReadingCard: ...
