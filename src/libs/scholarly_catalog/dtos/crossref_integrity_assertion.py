from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrossrefIntegrityEntry:
    wire_direction: str
    ordinal: int
    counterparty_doi_raw: str | None
    type_raw: str | None
    source_raw: str | None
    label_raw: str | None
    record_id_raw_json: str | None
    event_class: str
    updated_value: str | None
    updated_precision: str | None
    updated_raw_json: str | None
    raw_json: str


@dataclass(frozen=True)
class CrossrefIntegrityGap:
    path: str
    error_code: str
    raw_json: str


@dataclass(frozen=True)
class CrossrefIntegrityAssertionDraft:
    record_canonical_doi: str
    provider_revision_id: str
    wire_direction: str
    update_ordinal: int
    counterparty_doi_raw: str | None
    counterparty_canonical_doi: str | None
    counterparty_normalization_state: str
    notice_canonical_doi: str | None
    target_canonical_doi: str | None
    type_raw: str | None
    source_raw: str | None
    label_raw: str | None
    record_id_raw_json: str | None
    event_class: str
    updated_value: str | None
    updated_precision: str | None
    updated_raw_json: str | None
    raw_json: str
    observed_at: datetime


@dataclass(frozen=True)
class CrossrefIntegrityGapDraft:
    provider_revision_id: str
    path: str
    error_code: str
    raw_json: str
    observed_at: datetime

@dataclass(frozen=True)
class CrossrefIntegrityAssertionRef:
    assertion_id: str
    notice_canonical_doi: str | None
    target_canonical_doi: str | None


@dataclass(frozen=True)
class CrossrefIntegrityWorkBindingDraft:
    assertion_id: str
    role: str
    canonical_doi: str
    manifestation_id: str
    work_id: str
    canonical_work_id: str
    bound_at: datetime
