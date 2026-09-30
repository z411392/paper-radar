from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ArxivSourceRecord:
    """Source observations only; links are untrusted data, never fetching authority."""

    source_record_id: str
    source_url: str
    arxiv_id: str
    version: int | None
    title: str
    abstract: str | None
    authors: tuple[tuple[str, tuple[str, ...]], ...]
    published_at: datetime
    updated_at: datetime
    categories: tuple[tuple[str, str | None], ...]
    primary_category: str | None
    doi: str | None
    journal_reference: str | None
    comment: str | None
    links: tuple[tuple[str, str | None, str | None, str | None], ...]
    missing_fields: tuple[str, ...]
