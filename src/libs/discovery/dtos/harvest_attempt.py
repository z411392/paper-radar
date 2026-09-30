from dataclasses import dataclass, field

from libs.discovery.dtos.source_page_request import SourcePageRequest


@dataclass(frozen=True)
class HarvestAttempt:
    attempt_id: str
    unit_id: str
    attempt_no: int
    request: SourcePageRequest = field(repr=False)
    started_at: str
    state: str
    capture_json: str | None = field(default=None, repr=False)
    finished_at: str | None = None
