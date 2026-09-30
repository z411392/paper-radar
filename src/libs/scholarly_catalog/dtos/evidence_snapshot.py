from dataclasses import dataclass

from libs.scholarly_catalog.dtos.evidence_anchor import EvidenceAnchor


@dataclass(frozen=True)
class EvidenceSnapshot:
    snapshot_id: str
    revision_id: str
    work_id: str
    object_id: str
    text_object_id: str
    parser_version: str
    evidence_level: str
    coverage_json: str
    fingerprint: str
    created_at: str
    anchors: tuple[EvidenceAnchor, ...]
