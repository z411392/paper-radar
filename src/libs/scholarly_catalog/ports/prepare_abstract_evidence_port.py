from typing import Protocol

from libs.scholarly_catalog.dtos.abstract_evidence import (
    AbstractEvidenceRequest,
    AbstractEvidenceResult,
)


class PrepareAbstractEvidencePort(Protocol):
    def __call__(
        self,
        request: AbstractEvidenceRequest,
    ) -> AbstractEvidenceResult: ...
