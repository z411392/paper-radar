from dataclasses import dataclass


@dataclass(frozen=True)
class CrossrefCoveragePassEvidence:
    pass_no: int
    state: str
    traversal_complete: bool
    accounting_complete: bool
    first_reported_total: int | None
    raw_item_count: int
    processed_item_count: int
    quarantine_count: int
    unique_doi_count: int
    duplicate_doi_count: int
    parse_gap_count: int
    drift_suspected: bool
    repair_pending: bool
    source_completeness: str
    error_code: str | None


@dataclass(frozen=True)
class CrossrefCoverageGeneration:
    window_id: str
    query_fingerprint: str
    config_version: str
    requested_rows: int
    window_state: str
    changed_provider_revision_count: int
    stream_finalized_until: str | None
    safety_lag_seconds: None
    passes: tuple[CrossrefCoveragePassEvidence, ...]


@dataclass(frozen=True)
class HarvestCoverageWindow:
    business_key: str
    binding_key: str
    profile_id: str
    profile_revision: int
    domain_id: str
    domain_revision: int
    source_id: str
    window_start: str
    window_end: str
    workflow_state: str
    last_error_code: str | None
    population_recall: None
    crossref_generations: tuple[CrossrefCoverageGeneration, ...]
