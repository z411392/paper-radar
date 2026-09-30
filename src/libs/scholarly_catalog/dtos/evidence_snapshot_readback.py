from dataclasses import dataclass

from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot


@dataclass(frozen=True)
class EvidenceSnapshotReadback:
    snapshot: EvidenceSnapshot
    source_bytes: bytes
    normalized_text: str
