from typing import Protocol

from libs.watch_profiles.dtos.domain_definition import DomainDefinition


class ReadDomainDefinitionPort(Protocol):
    def __call__(self, domain_id: str, revision: int) -> DomainDefinition: ...
