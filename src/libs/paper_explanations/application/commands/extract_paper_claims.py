from libs.paper_explanations.domain.services.claim_extraction_rules import ClaimExtractionRules
from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.paper_explanations.ports.claim_candidate_port import ClaimCandidatePort
from libs.scholarly_catalog.ports.read_evidence_snapshot_port import ReadEvidenceSnapshotPort


class ExtractPaperClaims:
    def __init__(self, evidence: ReadEvidenceSnapshotPort, model: ClaimCandidatePort) -> None:
        self._evidence = evidence
        self._model = model

    def __call__(self, snapshot_id: str) -> ClaimExtractionResult:
        ClaimExtractionRules.snapshot_id(snapshot_id)
        request = ClaimExtractionRules.request(snapshot_id, self._evidence(snapshot_id))
        return ClaimExtractionRules.parse(request, self._model(request))
