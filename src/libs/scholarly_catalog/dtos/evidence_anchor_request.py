from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceAnchorRequest:
    quote: str
    offset_start: int
    offset_end: int
    section_label: str | None = None
    paragraph_id: str | None = None
    table_locator_json: str | None = None
