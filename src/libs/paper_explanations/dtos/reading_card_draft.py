from dataclasses import dataclass, field

from libs.paper_explanations.dtos.structured_generation_result import GenerationReceipt


@dataclass(frozen=True)
class TranslationPassage:
    text: str = field(repr=False)
    anchor_ids: tuple[str, ...]


@dataclass(frozen=True)
class CardStatement:
    claim_type: str
    text: str = field(repr=False)
    claim_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReadingCardDraft:
    snapshot_id: str
    revision_id: str
    work_id: str
    input_fingerprint: str
    original_abstract: str = field(repr=False)
    faithful_translation: tuple[TranslationPassage, ...]
    plain_language_card: tuple[CardStatement, ...]
    not_reported_in_read_evidence: tuple[str, ...]
    receipt: GenerationReceipt
    evidence_level: str = "abstract_only"
    target_language: str = "zh-TW"
    validation_state: str = "draft_requires_verification"
