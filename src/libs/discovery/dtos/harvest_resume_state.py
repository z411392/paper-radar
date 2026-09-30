from dataclasses import dataclass, field

from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.harvest_page_result import HarvestPageResult


@dataclass(frozen=True)
class HarvestResumeState:
    """Current unit progress and a candidate for its next offset, not a past receipt."""

    unit_id: str
    query_fingerprint: str
    checkpoint_version: int
    next_start: int
    total_results: int | None
    state: str
    attempt: HarvestAttempt | None = field(default=None, repr=False)
    previous_result: HarvestPageResult | None = None
