from typing import Protocol

from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot


class EvidenceSnapshotStorePort(Protocol):
    def save(self, snapshot: EvidenceSnapshot) -> EvidenceSnapshot: ...

    def read(self, snapshot_id: str) -> EvidenceSnapshot: ...
