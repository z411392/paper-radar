from typing import Protocol

from libs.paper_explanations.dtos.explanation_verification import (
    SupportVerificationCandidate,
    SupportVerificationRequest,
)


class SupportVerifierPort(Protocol):
    def __call__(self, request: SupportVerificationRequest) -> SupportVerificationCandidate: ...
