from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_window_split import CrossrefWindowSplit


class SplitCrossrefWindowPort(Protocol):
    def __call__(self, plan: CrossrefWindowPlan, *, split_at: datetime,
                 reason: str, created_at: datetime) -> CrossrefWindowSplit: ...
