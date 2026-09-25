from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrossrefProviderRevisionDraft:
    canonical_doi: str
    raw_doi: str
    provider_sha256: str
    semantic_sha256: str
    semantic_version: str
    canonical_json: str
    title: str | None
    indexed_at: str | None
    created_at: str | None
    deposited_at: str | None
    published_date: str | None
    published_precision: str | None
    parse_warnings: tuple[str, ...]
    page_id: str
    ordinal: int
    observed_at: datetime


@dataclass(frozen=True)
class CrossrefProviderProjection:
    canonical_doi: str
    provider_revision_id: str
    semantic_sha256: str
    title: str | None
    published_date: str | None
    published_precision: str | None
    replayed: bool
