from dataclasses import dataclass


@dataclass(frozen=True)
class PubmedWindowRunResult:
    state: str
    stop_reason: str
    fetched_requests: int
    processed_pages: int
    next_start: int
    total_results: int | None
