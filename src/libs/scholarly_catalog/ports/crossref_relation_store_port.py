from typing import Protocol

from libs.scholarly_catalog.dtos.crossref_relation_assertion import (
    CrossrefRelationAssertionDraft,
    CrossrefRelationGapDraft,
    CrossrefRelationLifecycle,
)


class CrossrefRelationStorePort(Protocol):
    def register(
        self,
        assertions: tuple[CrossrefRelationAssertionDraft, ...],
        gaps: tuple[CrossrefRelationGapDraft, ...],
        *,
        source_canonical_doi: str | None = None,
        provider_revision_id: str | None = None,
        snapshot_complete: bool | None = None,
    ) -> None: ...

    def lifecycle(
        self,
        source_canonical_doi: str,
    ) -> tuple[CrossrefRelationLifecycle, ...]: ...
