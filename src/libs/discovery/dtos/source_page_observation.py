from dataclasses import dataclass


@dataclass(frozen=True)
class SourcePageObservation:
    """Parsed page contract, not evidence of a successful HTTP call or durable write."""

    source_id: str
    query_fingerprint: str
    start_index: int
    total_results: int
    record_ids: tuple[str, ...]
    status: str = "ok"
