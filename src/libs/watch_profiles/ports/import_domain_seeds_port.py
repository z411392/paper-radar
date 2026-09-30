from typing import Protocol

from libs.watch_profiles.dtos.domain_seed_outcome import DomainSeedOutcome


class ImportDomainSeedsPort(Protocol):
    def __call__(self, payload: str) -> tuple[DomainSeedOutcome, ...]: ...
