from dataclasses import dataclass

from libs.paper_explanations.dtos.explanation_verification import (
    ExplanationVerificationResult,
)


@dataclass(frozen=True)
class TrackedExplanationVerification:
    verification: ExplanationVerificationResult
    support_run_id: str | None
    support_generation_fingerprint: str | None
