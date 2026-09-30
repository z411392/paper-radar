from typing import Protocol

from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityWorkBindingDraft,
)


class CrossrefIntegrityWorkBindingStorePort(Protocol):
    def register(self, draft: CrossrefIntegrityWorkBindingDraft) -> None: ...
