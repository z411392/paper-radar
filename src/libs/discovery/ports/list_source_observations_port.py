from typing import Protocol

from libs.discovery.dtos.source_observation_page import SourceObservationPage


class ListSourceObservationsPort(Protocol):
    def __call__(
        self,
        unit_id: str,
        source: str,
        *,
        after_observation_id: str | None,
        limit: int,
    ) -> SourceObservationPage: ...
