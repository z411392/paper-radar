from datetime import datetime
from typing import Protocol

from libs.research_workflow.dtos.scheduler import SchedulerTickResult


class RunSchedulerTickPort(Protocol):
    def __call__(
        self,
        *,
        now: datetime,
        max_new_jobs: int = 20,
    ) -> SchedulerTickResult: ...
