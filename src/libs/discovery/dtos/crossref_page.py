"""Protocol values only: decoded items are not durable catalog projections."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class CrossrefWindowInput:
    binding_key: str
    scope_query: str = field(repr=False)
    from_index: datetime
    until_index: datetime
    contact_email: str = field(repr=False)
    config_version: str
    rows: int = 1000


@dataclass(frozen=True)
class CrossrefWindowPlan:
    definition: CrossrefWindowInput
    parameters: tuple[tuple[str, str], ...] = field(repr=False)
    query_fingerprint: str
    parameters_fingerprint: str


@dataclass(frozen=True)
class CrossrefPageRequest:
    query_fingerprint: str
    parameters_fingerprint: str
    cursor: str = field(repr=False)
    url: str = field(repr=False)
    request_fingerprint: str


@dataclass(frozen=True)
class CrossrefDecodedItem:
    ordinal: int
    state: Literal["decoded", "quarantined"]
    doi_raw: str | None = field(repr=False)
    canonical_json: str = field(repr=False)
    canonical_sha256: str
    error_code: str | None


@dataclass(frozen=True)
class CrossrefDecodedPage:
    parser_version: str
    query_fingerprint: str
    parameters_fingerprint: str
    request_fingerprint: str
    response_sha256: str
    cursor_in: str = field(repr=False)
    next_cursor: str | None = field(repr=False)
    reported_total: int | None
    items: tuple[CrossrefDecodedItem, ...] = field(repr=False)
    traversal_end_hint: bool
