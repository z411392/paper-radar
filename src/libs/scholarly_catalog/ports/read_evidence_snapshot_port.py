from typing import Protocol

from libs.scholarly_catalog.dtos.evidence_snapshot_readback import EvidenceSnapshotReadback


class ReadEvidenceSnapshotPort(Protocol):
    def __call__(self, snapshot_id: str) -> EvidenceSnapshotReadback: ...
