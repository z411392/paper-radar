from typing import Protocol

from libs.scholarly_catalog.dtos.paper_identity_observation import PaperIdentityObservation
from libs.scholarly_catalog.dtos.paper_identity_resolution import PaperIdentityResolution


class ResolvePaperIdentityPort(Protocol):
    def __call__(self, observation: PaperIdentityObservation) -> PaperIdentityResolution: ...
