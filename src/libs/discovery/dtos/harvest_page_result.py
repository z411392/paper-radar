from dataclasses import dataclass


@dataclass(frozen=True)
class HarvestPageResult:
    """Historical result of one processing command, not the unit's live status."""

    attempt_id: str
    unit_id: str
    processor_version: str
    parser_version: str
    expected_checkpoint_version: int
    checkpoint_version: int
    next_start: int
    total_results: int | None
    state: str
    observation_ids: tuple[str, ...]
    error_code: str | None
    processed_at: str
