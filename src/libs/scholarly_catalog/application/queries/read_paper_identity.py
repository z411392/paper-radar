from libs.scholarly_catalog.domain.services.normalize_paper_identifier import NormalizePaperIdentifier
from libs.scholarly_catalog.dtos.paper_identity_view import PaperIdentityView
from libs.scholarly_catalog.ports.paper_identity_store_port import PaperIdentityStorePort


class ReadPaperIdentity:
    def __init__(self, normalize: NormalizePaperIdentifier, store: PaperIdentityStorePort) -> None:
        self._normalize = normalize
        self._store = store

    def __call__(self, namespace: str, value: str) -> PaperIdentityView:
        return self._store.read(self._normalize(namespace, value))
