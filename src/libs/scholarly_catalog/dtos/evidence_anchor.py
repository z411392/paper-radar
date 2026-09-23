from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceAnchor:
    anchor_id: str
    snapshot_id: str
    section_label: str | None
    paragraph_id: str | None
    quote: str
    offset_start: int
    offset_end: int
    table_locator_json: str | None
