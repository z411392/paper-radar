from typing import Protocol

from libs.paper_explanations.dtos.structured_generation_request import StructuredGenerationRequest
from libs.paper_explanations.dtos.structured_generation_result import StructuredGenerationResult


class StructuredGenerationPort(Protocol):
    def __call__(self, request: StructuredGenerationRequest) -> StructuredGenerationResult: ...
