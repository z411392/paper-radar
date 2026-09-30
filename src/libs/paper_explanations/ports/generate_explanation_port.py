from typing import Protocol

from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.paper_explanations.dtos.reading_card_draft import ReadingCardDraft


class GenerateExplanationPort(Protocol):
    def __call__(
        self, claims: ClaimExtractionResult, *, glossary: tuple[tuple[str, str], ...] = (),
    ) -> ReadingCardDraft: ...
