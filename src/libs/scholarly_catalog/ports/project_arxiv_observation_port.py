from typing import Protocol

from libs.discovery.dtos.arxiv_observation_replay import ArxivObservationReplay
from libs.scholarly_catalog.dtos.arxiv_catalog_projection import ArxivCatalogProjection


class ProjectArxivObservationPort(Protocol):
    def __call__(
        self,
        replay: ArxivObservationReplay,
    ) -> ArxivCatalogProjection: ...
