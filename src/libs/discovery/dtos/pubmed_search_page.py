from dataclasses import dataclass

from libs.discovery.dtos.source_page_observation import SourcePageObservation


@dataclass(frozen=True)
class PubmedSearchPage:
    observation: SourcePageObservation
    pmids: tuple[str, ...]
    raw_body: bytes
    response_sha256: str
    request_fingerprint: str
    parser_version: str
