from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrossrefIntegrityEntry:
    ordinal: int
    target_doi_raw: str | None
    type_raw: str | None
    source_raw: str | None
    label_raw: str | None
    record_id_raw: str | None
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
    source_notice_doi: str
    provider_revision_id: str
    update_ordinal: int
    target_doi_raw: str | None
    target_canonical_doi: str | None
    target_normalization_state: str
    type_raw: str | None
    source_raw: str | None
    label_raw: str | None
    record_id_raw: str | None
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
