from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PubmedPendingSearchPage:
    start_index: int
    pmids: tuple[str, ...]
    raw_object_id: str
    request_fingerprint: str
    response_sha256: str
    observed_at: datetime


@dataclass(frozen=True)
class PubmedWindowProgress:
    business_key: str
    query_fingerprint: str
    window_start: datetime
    window_end: datetime
    next_start: int
    total_results: int | None
    state: str
    pending_page: PubmedPendingSearchPage | None


@dataclass(frozen=True)
class PreparedPubmedBibliographyObservation:
    observation_id: str
    business_key: str
    start_index: int
    pmid: str
    raw_object_id: str
    request_fingerprint: str
    response_sha256: str
    parser_version: str
    record_json: str
    content_fingerprint: str
    observed_at: datetime
