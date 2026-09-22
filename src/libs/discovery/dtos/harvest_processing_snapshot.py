from dataclasses import dataclass, field

from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.harvest_page_result import HarvestPageResult


@dataclass(frozen=True)
class HarvestProcessingSnapshot:
    attempt: HarvestAttempt
    checkpoint_version: int
    next_start: int
    total_results: int | None
    unit_state: str
    parser_version: str
    record_ids: tuple[str, ...] = field(repr=False)
    envelope_json: str = field(repr=False)
    cursor_json: str | None = field(repr=False)
    coverage_json: str = field(repr=False)
    previous_result: HarvestPageResult | None = None
