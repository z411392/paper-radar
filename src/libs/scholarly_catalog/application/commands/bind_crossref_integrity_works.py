from datetime import datetime

from libs.scholarly_catalog.dtos.crossref_integrity_assertion import (
    CrossrefIntegrityAssertionRef,
    CrossrefIntegrityWorkBindingDraft,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError
from libs.scholarly_catalog.ports.crossref_integrity_work_binding_store_port import (
    CrossrefIntegrityWorkBindingStorePort,
)
from libs.scholarly_catalog.ports.read_paper_identity_port import ReadPaperIdentityPort


class BindCrossrefIntegrityWorks:
    def __init__(
        self,
        read_identity: ReadPaperIdentityPort,
        store: CrossrefIntegrityWorkBindingStorePort,
    ) -> None:
        self._read_identity = read_identity
        self._store = store

    def __call__(
        self,
        assertions: tuple[CrossrefIntegrityAssertionRef, ...],
        *,
        observed_at: datetime,
    ) -> None:
        if not isinstance(assertions, tuple):
            raise CrossrefProviderProjectionError(
                "invalid_crossref_integrity_binding"
            )
        for assertion in assertions:
            if not isinstance(assertion, CrossrefIntegrityAssertionRef):
                raise CrossrefProviderProjectionError(
                    "invalid_crossref_integrity_binding"
                )
            for role, doi in (
                ("notice", assertion.notice_canonical_doi),
                ("target", assertion.target_canonical_doi),
            ):
                if doi is None:
                    continue
                try:
                    identity = self._read_identity("doi", doi)
                except PaperIdentityError as exc:
                    if exc.code == "identity_missing":
                        continue
                    raise CrossrefProviderProjectionError(
                        "crossref_integrity_identity_lookup_failed"
                    ) from exc
                self._store.register(
                    CrossrefIntegrityWorkBindingDraft(
                        assertion.assertion_id,
                        role,
                        doi,
                        identity.manifestation_id,
                        identity.work_id,
                        identity.canonical_work_id,
                        observed_at,
                    )
                )
