from dataclasses import dataclass

from libs.discovery.dtos.pubmed_harvest_state import PubmedHarvestState


@dataclass(frozen=True)
class PubmedHarvestRunResult:
    progress: PubmedHarvestState
    stop_reason: str
    search_fetches: int
    bibliography_fetches: int
