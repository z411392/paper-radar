from typing import Protocol

from libs.research_workflow.dtos.evidence_explanation import (
    EvidenceExplanationRequest,
    EvidenceExplanationResult,
)


class ProcessEvidenceExplanationPort(Protocol):
    def __call__(
        self,
        request: EvidenceExplanationRequest,
    ) -> EvidenceExplanationResult: ...
