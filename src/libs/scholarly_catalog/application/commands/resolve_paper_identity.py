from libs.scholarly_catalog.domain.services.normalize_paper_identifier import NormalizePaperIdentifier
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.dtos.paper_identity_resolution import PaperIdentityResolution
from libs.scholarly_catalog.ports.paper_identity_store_port import PaperIdentityStorePort


class ResolvePaperIdentity:
    def __init__(self, normalize: NormalizePaperIdentifier, store: PaperIdentityStorePort) -> None:
        self._normalize = normalize
        self._store = store

    def __call__(self, observation: PaperIdentityObservation) -> PaperIdentityResolution:
        identifier = self._normalize(observation.identifier_namespace, observation.identifier_value)
        return self._store.register(observation, identifier)
