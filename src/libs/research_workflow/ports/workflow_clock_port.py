from datetime import datetime
from typing import Protocol


class WorkflowClockPort(Protocol):
    def now(self) -> datetime: ...
