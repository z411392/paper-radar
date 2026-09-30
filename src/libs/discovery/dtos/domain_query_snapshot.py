from dataclasses import dataclass


@dataclass(frozen=True)
class DomainQuerySnapshot:
    """Provider-specific query projection of one exact, published domain revision."""

    domain_id: str
    revision: int
    sources: tuple[str, ...]
    categories: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
