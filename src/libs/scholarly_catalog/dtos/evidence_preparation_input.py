from dataclasses import dataclass
from datetime import datetime

from libs.scholarly_catalog.dtos.evidence_anchor_request import EvidenceAnchorRequest


@dataclass(frozen=True)
class EvidencePreparationInput:
    revision_id: str
    work_id: str
    source_bytes: bytes
    source_media_type: str
    parser_version: str
    normalized_text: str | None
    content_scope: str
    document_complete: bool
    included_sections: tuple[str, ...]
    missing_required_sections: tuple[str, ...]
    parser_error_code: str | None
    anchors: tuple[EvidenceAnchorRequest, ...]
    created_at: datetime
