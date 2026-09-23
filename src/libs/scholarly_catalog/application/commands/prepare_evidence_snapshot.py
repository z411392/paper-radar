from libs.scholarly_catalog.domain.services.evidence_snapshot_rules import EvidenceSnapshotRules
from libs.scholarly_catalog.dtos.evidence_preparation_input import EvidencePreparationInput
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.ports.evidence_object_store_port import EvidenceObjectStorePort
from libs.scholarly_catalog.ports.evidence_snapshot_store_port import EvidenceSnapshotStorePort


class PrepareEvidenceSnapshot:
    def __init__(
        self,
        rules: EvidenceSnapshotRules,
        objects: EvidenceObjectStorePort,
        snapshots: EvidenceSnapshotStorePort,
    ) -> None:
        self._rules = rules
        self._objects = objects
        self._snapshots = snapshots

    def __call__(self, value: EvidencePreparationInput) -> EvidenceSnapshot:
        # All parser/coverage/anchor checks happen before object side effects.
        level = self._rules.preview_level(value)
        source = self._objects.publish_source(
            value.source_bytes,
            value.source_media_type,
            abstract_only=level == "abstract_only",
        )
        if value.normalized_text is None:
            raise AssertionError("validated evidence input must contain normalized text")
        text = self._objects.publish_text(value.normalized_text.encode("utf-8"))
        snapshot = self._rules.prepare(value, source, text)
        return self._snapshots.save(snapshot)
