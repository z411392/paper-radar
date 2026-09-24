from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan


class CrossrefCaptureClaimStorePort(Protocol):
    def reserve(
        self, plan: CrossrefWindowPlan, pass_id: str, request: CrossrefPageRequest, *,
        owner_id: str, expected_workspace_id: str, expected_epoch: int,
        now: datetime, lease_seconds: int,
    ) -> CrossrefCaptureClaim: ...

    def begin_dispatch(self, claim: CrossrefCaptureClaim, *, now: datetime) -> None:
        """One-shot local send boundary, not replayable and not a provider rate grant."""
        ...

    def release(self, claim: CrossrefCaptureClaim, *, now: datetime) -> None: ...

    def read(self, claim_id: str) -> CrossrefCaptureClaim: ...
