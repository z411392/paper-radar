from dataclasses import dataclass

from libs.paper_explanations.dtos.structured_generation_result import (
    StructuredGenerationResult,
)


@dataclass(frozen=True)
class BudgetedGenerationExecution:
    run_id: str
    generation_fingerprint: str
    result: StructuredGenerationResult
    cached: bool
