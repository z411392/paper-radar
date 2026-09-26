from dataclasses import dataclass


@dataclass(frozen=True)
class SourceObservationPage:
    unit_id: str
    source: str
    observation_ids: tuple[str, ...]
    complete: bool
