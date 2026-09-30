from dataclasses import dataclass


@dataclass(frozen=True)
class DomainDefinition:
    domain_id: str
    revision: int
    name: str
    aliases: tuple[str, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    sources: tuple[str, ...]
    source_categories: tuple[tuple[str, tuple[str, ...]], ...]
