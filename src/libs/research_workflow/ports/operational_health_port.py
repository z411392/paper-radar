from typing import Protocol

from libs.research_workflow.dtos.operational_health import (
    DeliveryHealthEvidence,
    ExplanationHealthEvidence,
    OperationalHealthReport,
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
