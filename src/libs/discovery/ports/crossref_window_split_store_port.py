from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_window_split import CrossrefWindowSplit


class CrossrefWindowSplitStorePort(Protocol):
    def split(
        self,
        parent: CrossrefWindowPlan,
        left: CrossrefWindowPlan,
        right: CrossrefWindowPlan,
        *,
        reason: str,
        created_at: datetime,
    ) -> CrossrefWindowSplit: ...
