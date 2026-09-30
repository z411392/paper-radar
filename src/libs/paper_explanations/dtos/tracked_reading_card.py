from dataclasses import dataclass

from libs.paper_explanations.dtos.reading_card_draft import ReadingCardDraft


@dataclass(frozen=True)
class TrackedReadingCard:
    draft: ReadingCardDraft
    run_id: str
    generation_fingerprint: str
