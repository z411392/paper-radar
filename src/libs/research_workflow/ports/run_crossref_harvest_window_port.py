from typing import Protocol

from libs.discovery.dtos.crossref_harvest import CrossrefHarvestStepResult
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan


class RunCrossrefHarvestWindowPort(Protocol):
    def __call__(
        self,
        plan: CrossrefWindowPlan,
        *,
        owner_id: str,
        max_pages: int,
        lease_seconds: int,
    ) -> CrossrefHarvestStepResult: ...
