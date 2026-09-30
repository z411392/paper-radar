from libs.paper_explanations.domain.services.reading_card_rules import ReadingCardRules
from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.paper_explanations.dtos.tracked_reading_card import TrackedReadingCard
from libs.paper_explanations.ports.execute_budgeted_generation_port import (
    ExecuteBudgetedGenerationPort,
)
from libs.scholarly_catalog.ports.read_evidence_snapshot_port import (
    ReadEvidenceSnapshotPort,
)


class GenerateTrackedReadingCard:
    def __init__(
        self,
        evidence: ReadEvidenceSnapshotPort,
        generation: ExecuteBudgetedGenerationPort,
    ) -> None:
        self._evidence = evidence
        self._generation = generation

    def __call__(
        self,
        claims: ClaimExtractionResult,
        *,
        glossary: tuple[tuple[str, str], ...] = (),
    ) -> TrackedReadingCard:
        evidence = self._evidence(claims.request.snapshot_id)
        request = ReadingCardRules.request(claims, evidence, glossary)
        execution = self._generation.execute(request)
        return TrackedReadingCard(
            ReadingCardRules.parse(claims, request, execution.result),
            execution.run_id,
            execution.generation_fingerprint,
        )
