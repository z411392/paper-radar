from dataclasses import dataclass

from libs.discovery.dtos.harvest_resume_state import HarvestResumeState


@dataclass(frozen=True)
class HarvestRunResult:
    progress: HarvestResumeState
    stop_reason: str
    fetch_count: int
    processed_pages: int
    reused_captures: int
