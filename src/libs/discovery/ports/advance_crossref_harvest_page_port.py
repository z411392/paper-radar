from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_harvest import CrossrefHarvestStepResult
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan


class AdvanceCrossrefHarvestPagePort(Protocol):
    def __call__(
        self,
        plan: CrossrefWindowPlan,
        *,
        now: datetime,
    ) -> CrossrefHarvestStepResult: ...
