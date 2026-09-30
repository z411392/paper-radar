from datetime import datetime
from typing import Protocol

from libs.scholarly_catalog.dtos.digest_research_event import DigestResearchEvent


class ListDigestResearchEventsPort(Protocol):
    def __call__(
        self,
        period_start: datetime,
        cutoff_at: datetime,
    ) -> tuple[DigestResearchEvent, ...]: ...
