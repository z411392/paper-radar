from typing import Protocol

from libs.scholarly_catalog.dtos.evidence_preparation_input import EvidencePreparationInput
from libs.scholarly_catalog.dtos.evidence_snapshot import EvidenceSnapshot


class PrepareEvidenceSnapshotPort(Protocol):
    def __call__(self, value: EvidencePreparationInput) -> EvidenceSnapshot: ...
