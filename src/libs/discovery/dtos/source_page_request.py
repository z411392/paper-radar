from dataclasses import dataclass


@dataclass(frozen=True)
class SourcePageRequest:
    source_id: str
    query_fingerprint: str
    request_fingerprint: str
    method: str
    url: str
    start: int
    max_results: int
    maximum_window_results: int
