from dataclasses import dataclass


@dataclass(frozen=True)
class PubmedBibliographyRecord:
    pmid: str
    title: str
    abstract: str | None
    authors: tuple[str, ...]
    journal_title: str | None
    publication_date: str | None
    publication_date_precision: str | None
    doi: str | None
    pmcid: str | None
    languages: tuple[str, ...]
    publication_types: tuple[str, ...]
