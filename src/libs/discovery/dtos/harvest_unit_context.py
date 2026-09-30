from dataclasses import dataclass


@dataclass(frozen=True)
class HarvestUnitContext:
    unit_id: str
    source: str
    profile_id: str
    profile_revision: int
    domain_id: str
    domain_revision: int
