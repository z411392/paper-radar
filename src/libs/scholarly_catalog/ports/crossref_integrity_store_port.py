from typing import Protocol

from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityAssertionDraft,
    CrossrefIntegrityGapDraft,
)


class CrossrefIntegrityStorePort(Protocol):
    def register(
        self,
        assertions: tuple[CrossrefIntegrityAssertionDraft, ...],
        gaps: tuple[CrossrefIntegrityGapDraft, ...],
    ) -> None: ...
