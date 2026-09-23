from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import EvidenceSnapshotRules
from libs.scholarly_catalog.dtos.evidence_snapshot_readback import EvidenceSnapshotReadback
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError
from libs.scholarly_catalog.ports.evidence_object_store_port import EvidenceObjectStorePort
from libs.scholarly_catalog.ports.evidence_snapshot_store_port import EvidenceSnapshotStorePort


class ReadEvidenceSnapshot:
    def __init__(
        self,
        rules: EvidenceSnapshotRules,
        objects: EvidenceObjectStorePort,
        snapshots: EvidenceSnapshotStorePort,
    ) -> None:
        self._rules = rules
        self._objects = objects
        self._snapshots = snapshots

    def __call__(self, snapshot_id: str) -> EvidenceSnapshotReadback:
        snapshot = self._snapshots.read(snapshot_id)
        source = self._objects.read(snapshot.object_id)
        encoded = self._objects.read(snapshot.text_object_id)
        try:
            text = encoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EvidenceSnapshotError("evidence_text_not_utf8") from exc
        self._rules.verify(snapshot, source, text)
        return EvidenceSnapshotReadback(snapshot, source, text)
