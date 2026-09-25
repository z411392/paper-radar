from typing import Protocol

from libs.discovery.dtos.pubmed_observation_replay import PubmedObservationReplay
from libs.scholarly_catalog.dtos.pubmed_catalog_projection import PubmedCatalogProjection


class ProjectPubmedObservationPort(Protocol):
    def __call__(
        self,
        replay: PubmedObservationReplay,
    ) -> PubmedCatalogProjection: ...
