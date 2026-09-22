from dataclasses import dataclass


@dataclass(frozen=True)
class DeferredFilter:
    name: str
    value_json: str
    required_stage: str
    handling: str = "unsupported_at_source"


@dataclass(frozen=True)
class CompiledSourceQuery:
    source_id: str
    compiler_version: str
    capability_version: str
    query_fingerprint: str
    provenance_json: str
    search_query: str
    page_size: int
    maximum_window_results: int
    deferred_filters: tuple[DeferredFilter, ...]
    warnings: tuple[str, ...]
