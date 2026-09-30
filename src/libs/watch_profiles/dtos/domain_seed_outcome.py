from dataclasses import dataclass


@dataclass(frozen=True)
class DomainSeedOutcome:
    domain_id: str
    revision: int
    disposition: str
