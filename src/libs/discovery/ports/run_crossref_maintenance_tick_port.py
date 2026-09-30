from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_repair import (
    CrossrefMaintenanceResult,
    CrossrefRepairPolicy,
)


class RunCrossrefMaintenanceTickPort(Protocol):
    def __call__(
        self,
        plan: CrossrefWindowPlan,
        *,
        now: datetime,
        policy: CrossrefRepairPolicy,
    ) -> CrossrefMaintenanceResult: ...
