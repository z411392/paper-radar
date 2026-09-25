from datetime import datetime

from libs.discovery.dtos.crossref_harvest import CrossrefPendingItem
from libs.scholarly_catalog.domain.services.crossref_provider_revision_rules import (
    CrossrefProviderRevisionRules,
)
from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.dtos.crossref_provider_revision import (
    CrossrefProviderProjection,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError
from libs.scholarly_catalog.ports.crossref_provider_revision_store_port import (
    CrossrefProviderRevisionStorePort,
)


class ProjectCrossrefPendingItem:
    def __init__(
        self,
        normalize: NormalizePaperIdentifier,
        store: CrossrefProviderRevisionStorePort,
    ) -> None:
        self._normalize = normalize
        self._store = store

    def __call__(
        self,
        pending: CrossrefPendingItem,
        *,
        observed_at: datetime,
    ) -> CrossrefProviderProjection:
        if not isinstance(pending, CrossrefPendingItem) or pending.raw_doi is None:
            raise CrossrefProviderProjectionError("crossref_provider_doi_invalid")
        try:
            identifier = self._normalize("doi", pending.raw_doi)
        except PaperIdentityError as exc:
            raise CrossrefProviderProjectionError(
                "crossref_provider_doi_invalid"
            ) from exc
        draft = CrossrefProviderRevisionRules.draft(
            pending,
            canonical_doi=identifier.normalized_value,
            observed_at=observed_at,
        )
        return self._store.register(draft)
