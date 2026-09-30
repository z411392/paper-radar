from datetime import datetime
from typing import Protocol

from libs.research_workflow.dtos.scheduler import SchedulerSnapshot


class SchedulerInputPort(Protocol):
    def read(self, now: datetime) -> SchedulerSnapshot: ...
