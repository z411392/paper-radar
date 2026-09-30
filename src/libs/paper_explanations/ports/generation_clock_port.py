from datetime import datetime
from typing import Protocol


class GenerationClockPort(Protocol):
    def __call__(self) -> datetime: ...
