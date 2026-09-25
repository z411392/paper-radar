from datetime import datetime
from typing import Protocol


class ResearchEventStorePort(Protocol):
    def register(
        self,
        *,
        event_id: str,
        work_id: str,
        revision_id: str | None,
        event_kind: str,
        canonical_event_key: str,
        source_evidence_json: str,
        occurred_at: datetime | None,
        observed_at: datetime,
    ) -> str: ...
