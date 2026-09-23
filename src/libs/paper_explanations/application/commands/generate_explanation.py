from libs.paper_explanations.domain.services.reading_card_rules import ReadingCardRules
from libs.paper_explanations.dtos.claim_extraction_request import ClaimExtractionRequest
from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.paper_explanations.dtos.reading_card_draft import ReadingCardDraft
from libs.paper_explanations.exceptions.reading_card_error import ReadingCardError
from libs.paper_explanations.ports.structured_generation_port import StructuredGenerationPort
from libs.scholarly_catalog.ports.read_evidence_snapshot_port import ReadEvidenceSnapshotPort


class GenerateExplanation:
    def __init__(self, evidence: ReadEvidenceSnapshotPort, model: StructuredGenerationPort) -> None:
        self._evidence = evidence
        self._model = model

    def __call__(
        self, claims: ClaimExtractionResult, *, glossary: tuple[tuple[str, str], ...] = (),
    ) -> ReadingCardDraft:
        if (
            not isinstance(claims, ClaimExtractionResult)
            or not isinstance(claims.request, ClaimExtractionRequest)
        ):
            raise ReadingCardError("invalid_reading_claims")
        evidence = self._evidence(claims.request.snapshot_id)
        request = ReadingCardRules.request(claims, evidence, glossary)
        completion = self._model(request)
        try:
            return ReadingCardRules.parse(claims, request, completion)
        except ReadingCardError as exc:
            raise ReadingCardError(exc.code, getattr(completion, "receipt", None)) from None
