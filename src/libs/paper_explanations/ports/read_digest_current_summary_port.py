from typing import Protocol

from libs.paper_explanations.dtos.digest_current_summary import DigestCurrentSummary


class ReadDigestCurrentSummaryPort(Protocol):
    def __call__(
        self,
        work_id: str,
        revision_id: str,
    ) -> DigestCurrentSummary | None: ...
