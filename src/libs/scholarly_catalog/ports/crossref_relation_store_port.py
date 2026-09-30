from typing import Protocol

from libs.scholarly_catalog.dtos.crossref_relation_assertion import (
    CrossrefRelationAssertionDraft,
    CrossrefRelationGapDraft,
)


class CrossrefRelationStorePort(Protocol):
    def register(
        self,
        assertions: tuple[CrossrefRelationAssertionDraft, ...],
        gaps: tuple[CrossrefRelationGapDraft, ...],
    ) -> None: ...
