from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HarvestQueryRequest:
    profile_id: str
    domain_id: str
    window_start: datetime
    window_end: datetime
    source_id: str = "arxiv"
    deferred_mode: str = "reject"
    time_basis: str = "submittedDate"
    page_size: int = 200
    expected_profile_revision: int | None = None
    expected_domain_revision: int | None = None
