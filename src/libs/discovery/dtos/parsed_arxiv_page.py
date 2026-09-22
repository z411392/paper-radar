from dataclasses import dataclass
from datetime import datetime

from libs.discovery.dtos.arxiv_source_record import ArxivSourceRecord
from libs.discovery.dtos.source_page_observation import SourcePageObservation


@dataclass(frozen=True)
class ParsedArxivPage:
    """Replayable in-memory evidence, not a persisted observation or coverage receipt."""

    observation: SourcePageObservation
    records: tuple[ArxivSourceRecord, ...]
    raw_body: bytes
    response_sha256: str
    request_fingerprint: str
    parser_version: str
    feed_id: str | None
    feed_updated_at: datetime | None
    items_per_page: int
