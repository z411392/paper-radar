from dataclasses import dataclass


@dataclass(frozen=True)
class SourceCapabilities:
    source_id: str
    version: str
    native_filters: tuple[str, ...]
    unsupported_native_filters: tuple[str, ...]
    supported_time_bases: tuple[str, ...]
    minimum_request_interval_seconds: int
    maximum_connections: int
    maximum_page_size: int
    maximum_window_results: int
    snapshot_pagination: bool
    live_verified: bool
