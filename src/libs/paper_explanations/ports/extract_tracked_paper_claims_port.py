from typing import Protocol

from libs.paper_explanations.dtos.tracked_claim_extraction import (
    TrackedClaimExtraction,
)


class ExtractTrackedPaperClaimsPort(Protocol):
    def __call__(self, snapshot_id: str) -> TrackedClaimExtraction: ...
