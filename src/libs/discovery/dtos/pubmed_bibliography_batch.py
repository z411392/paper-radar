from dataclasses import dataclass

from libs.discovery.dtos.pubmed_bibliography_record import PubmedBibliographyRecord


@dataclass(frozen=True)
class PubmedBibliographyBatch:
    records: tuple[PubmedBibliographyRecord, ...]
    raw_body: bytes
    response_sha256: str
    request_fingerprint: str
    parser_version: str
