from typing import Protocol

from libs.discovery.dtos.pubmed_observation_replay import PubmedObservationReplay


class ReplayPubmedObservationPort(Protocol):
    def __call__(self, observation_id: str) -> PubmedObservationReplay: ...
