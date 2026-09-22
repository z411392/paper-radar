from typing import Protocol

from libs.scholarly_catalog.dtos.normalized_identifier import NormalizedIdentifier
from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.dtos.paper_identity_resolution import PaperIdentityResolution
from libs.scholarly_catalog.dtos.paper_identity_view import PaperIdentityView


class PaperIdentityStorePort(Protocol):
    def register(
        self,
        observation: PaperIdentityObservation,
        identifier: NormalizedIdentifier,
    ) -> PaperIdentityResolution: ...

    def read(self, identifier: NormalizedIdentifier) -> PaperIdentityView: ...
