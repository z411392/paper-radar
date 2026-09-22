from typing import Protocol

from libs.watch_profiles.dtos.configuration_document import ConfigurationDocument
from libs.watch_profiles.dtos.domain_definition import DomainDefinition
from libs.watch_profiles.dtos.domain_seed_outcome import DomainSeedOutcome
from libs.watch_profiles.dtos.profile_revision import ProfileRevision


class WatchProfileStorePort(Protocol):
    def import_domains(self, document: ConfigurationDocument) -> tuple[DomainSeedOutcome, ...]: ...

    def read_domain(self, domain_id: str, revision: int) -> DomainDefinition: ...

    def publish(self, document: ConfigurationDocument, expected_revision: int | None) -> ProfileRevision: ...

    def read(self, profile_id: str, revision: int | None = None) -> ProfileRevision: ...

    def set_lifecycle(self, profile_id: str, lifecycle: str) -> None: ...
