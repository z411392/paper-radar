from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrossrefRelationEntry:
    ordinal: int
    predicate_raw: str
    target_id_type_raw: str
    target_value_raw: str
    asserted_by_raw: str | None
    relation_class: str


@dataclass(frozen=True)
class CrossrefRelationGap:
    path: str
    error_code: str
    raw_json: str


@dataclass(frozen=True)
class CrossrefRelationAssertionDraft:
    source_canonical_doi: str
    provider_revision_id: str
    ordinal: int
    predicate_raw: str
    target_id_type_raw: str
    target_value_raw: str
    asserted_by_raw: str | None
    relation_class: str
    target_namespace: str | None
    target_normalized_value: str | None
    target_normalization_state: str
    observed_at: datetime


@dataclass(frozen=True)
class CrossrefRelationGapDraft:
    provider_revision_id: str
    path: str
    error_code: str
    raw_json: str
    observed_at: datetime
