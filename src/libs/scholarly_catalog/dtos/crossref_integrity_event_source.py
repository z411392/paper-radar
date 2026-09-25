from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrossrefIntegrityEventSource:
    assertion_id: str
    event_class: str
    wire_direction: str
    notice_canonical_doi: str | None
    target_canonical_doi: str | None
    type_raw: str | None
    source_raw: str | None
    label_raw: str | None
    record_id_raw_json: str | None
    updated_value: str | None
    updated_precision: str | None
    updated_raw_json: str | None
    raw_json: str
    first_observed_at: datetime
    canonical_work_id: str
