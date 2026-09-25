from typing import Protocol

from libs.scholarly_catalog.dtos.crossref_provider_revision import (
    CrossrefProviderProjection,
    CrossrefProviderRevisionDraft,
)


class CrossrefProviderRevisionStorePort(Protocol):
    def register(
        self,
        draft: CrossrefProviderRevisionDraft,
    ) -> CrossrefProviderProjection: ...
