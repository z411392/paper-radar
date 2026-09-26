from libs.scholarly_catalog.domain.services.normalize_paper_identifier import (
    NormalizePaperIdentifier,
)
from libs.scholarly_catalog.ports.paper_identity_store_port import (
    PaperIdentityStorePort,
)


class BindManifestationIdentifier:
    def __init__(
        self,
        normalize: NormalizePaperIdentifier,
        store: PaperIdentityStorePort,
    ) -> None:
        self._normalize = normalize
        self._store = store

    def __call__(
        self,
        *,
        manifestation_id: str,
        namespace: str,
        identifier_value: str,
        source_evidence_id: str,
    ) -> None:
        identifier = self._normalize(namespace, identifier_value)
        self._store.bind_identifier(
            manifestation_id,
            identifier,
            source_evidence_id,
        )
