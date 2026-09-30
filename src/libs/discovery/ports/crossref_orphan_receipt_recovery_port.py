from typing import Protocol

from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan
from libs.discovery.dtos.crossref_recovery import CrossrefRecoveredAttempt


class CrossrefOrphanReceiptRecoveryPort(Protocol):
    def find(
        self,
        plan: CrossrefWindowPlan,
        page_id: str,
        request: CrossrefPageRequest,
    ) -> CrossrefRecoveredAttempt | None: ...
