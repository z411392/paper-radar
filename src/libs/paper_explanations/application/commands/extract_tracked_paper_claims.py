from libs.paper_explanations.domain.services.claim_extraction_rules import (
    ClaimExtractionRules,
)
from libs.paper_explanations.dtos.claim_candidate import ClaimCandidate
from libs.paper_explanations.dtos.structured_generation_request import (
    StructuredGenerationRequest,
)
from libs.paper_explanations.dtos.tracked_claim_extraction import (
    TrackedClaimExtraction,
)
from libs.paper_explanations.ports.execute_budgeted_generation_port import (
    ExecuteBudgetedGenerationPort,
)
from libs.scholarly_catalog.ports.read_evidence_snapshot_port import (
    ReadEvidenceSnapshotPort,
)


class ExtractTrackedPaperClaims:
    def __init__(
        self,
        evidence: ReadEvidenceSnapshotPort,
        generation: ExecuteBudgetedGenerationPort,
    ) -> None:
        self._evidence = evidence
        self._generation = generation

    def __call__(self, snapshot_id: str) -> TrackedClaimExtraction:
        ClaimExtractionRules.snapshot_id(snapshot_id)
        request = ClaimExtractionRules.request(
            snapshot_id,
            self._evidence(snapshot_id),
        )
        execution = self._generation.execute(
            StructuredGenerationRequest(
                "claim_extraction",
                request.input_fingerprint,
                request.system_prompt,
                request.payload_json,
                "paper_claims",
                request.response_schema_json,
                request.model_name,
            )
        )
        finish = execution.result.receipt.finish_reason
        candidate = ClaimCandidate(
            execution.run_id,
            request.model_name,
            "" if finish is None else finish,
            execution.result.content_json,
        )
        return TrackedClaimExtraction(
            ClaimExtractionRules.parse(request, candidate),
            execution.run_id,
            execution.generation_fingerprint,
        )
