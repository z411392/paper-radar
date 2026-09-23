from libs.scholarly_catalog.dtos.evidence_preparation_input import EvidencePreparationInput
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot
from libs.scholarly_catalog.exceptions.evidence_snapshot_error import EvidenceSnapshotError


class EvidenceSnapshotRules:
    def preview_level(self, value: EvidencePreparationInput) -> str:
        raise EvidenceSnapshotError("not_implemented")

    def prepare(self, value: EvidencePreparationInput, source, text) -> EvidenceSnapshot:
        raise EvidenceSnapshotError("not_implemented")

    def verify(
        self,
        snapshot: EvidenceSnapshot,
        source_bytes: bytes,
        normalized_text: str,
    ) -> None:
        raise EvidenceSnapshotError("not_implemented")
