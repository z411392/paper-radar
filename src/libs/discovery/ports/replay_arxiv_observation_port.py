from typing import Protocol

from libs.discovery.dtos.arxiv_observation_replay import ArxivObservationReplay


class ReplayArxivObservationPort(Protocol):
    def __call__(self, observation_id: str) -> ArxivObservationReplay: ...
