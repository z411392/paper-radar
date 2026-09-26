from typing import Protocol

from libs.delivery.dtos.delivery_health import DeliveryHealthEvidence
from libs.paper_explanations.dtos.explanation_health import ExplanationHealthEvidence
from libs.research_workflow.dtos.operational_health import (
    OperationalHealthReport,
    RuntimeHealthEvidence,
    WorkflowHealthEvidence,
)


class ReadWorkflowHealthEvidencePort(Protocol):
    def __call__(self) -> tuple[WorkflowHealthEvidence, ...]: ...


class ReadExplanationHealthEvidencePort(Protocol):
    def __call__(self) -> ExplanationHealthEvidence | None: ...


class ReadDeliveryHealthEvidencePort(Protocol):
    def __call__(self) -> DeliveryHealthEvidence | None: ...


class InspectHealthPort(Protocol):
    def __call__(self) -> OperationalHealthReport: ...


class ReadRuntimeHealthEvidencePort(Protocol):
    def __call__(self) -> RuntimeHealthEvidence: ...
