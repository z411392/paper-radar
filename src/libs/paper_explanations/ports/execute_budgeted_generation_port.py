from typing import Protocol

from libs.paper_explanations.dtos.budgeted_generation_execution import (
    BudgetedGenerationExecution,
)
from libs.paper_explanations.dtos.structured_generation_request import (
    StructuredGenerationRequest,
)


class ExecuteBudgetedGenerationPort(Protocol):
    def execute(
        self,
        request: StructuredGenerationRequest,
    ) -> BudgetedGenerationExecution: ...
