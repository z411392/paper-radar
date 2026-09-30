from dataclasses import dataclass


@dataclass(frozen=True)
class CrossrefHarvestWindowState:
    window_id: str
    state: str
    binding_key: str
    query_fingerprint: str


@dataclass(frozen=True)
class CrossrefHarvestPassState:
    pass_id: str
    window_id: str
    pass_no: int
    parameters_fingerprint: str
    state: str
    current_cursor: str | None
    next_page_no: int
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
class CrossrefJournalPage:
    page_id: str
    pass_id: str
    page_no: int
    cursor_in: str
    request_fingerprint: str
    state: str
    last_error_code: str | None
    successful_receipt_id: str | None
    attempt_count: int
    cursor_out: str | None


@dataclass(frozen=True)
class CrossrefPendingItem:
    page_id: str
    ordinal: int
    raw_doi: str | None
    canonical_json: str
    canonical_sha256: str


@dataclass(frozen=True)
class CrossrefHarvestStepResult:
    state: str
    window_id: str
    pass_id: str
    page_id: str | None
    receipt_id: str | None
    pending_item_count: int
    error_code: str | None
