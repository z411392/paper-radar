from datetime import datetime
from typing import Protocol


class HarvestRuntimePort(Protocol):
    def now(self) -> datetime: ...

    def new_attempt_id(self) -> str: ...
