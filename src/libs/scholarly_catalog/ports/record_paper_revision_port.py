from datetime import datetime
from typing import Protocol


class RecordPaperRevisionPort(Protocol):
    def __call__(
        self,
        *,
        work_id: str,
        revision_id: str | None,
        event_kind: str,
        source_evidence_id: str,
        source_evidence: dict[str, object],
        observed_at: datetime,
        occurred_at: datetime | None = None,
    ) -> str: ...
