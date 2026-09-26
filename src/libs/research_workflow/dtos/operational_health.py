from dataclasses import dataclass

from libs.research_workflow.dtos.scheduler import CoverageGap


@dataclass(frozen=True)
class WorkflowHealthEvidence:
    business_key: str
    state: str
    created_at: str
    due_at: str
    last_error_code: str | None


@dataclass(frozen=True)
class ExplanationHealthEvidence:
    qa_rejected: int
    currency: str | None
    reserved_micros: int
    settled_actual_micros: int
    unknown_cost_reservations: int


@dataclass(frozen=True)
class DeliveryHealthEvidence:
    unknown_deliveries: int


@dataclass(frozen=True)
class SourceOperationalHealth:
    source_id: str
    latest_successful_window_end: str | None
    latest_observed_window_end: str
    failure_count: int
    pending_count: int
    oldest_pending_age_seconds: int | None
    last_error_code: str | None


@dataclass(frozen=True)
class OperationalHealthReport:
    sources: tuple[SourceOperationalHealth, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    explanations: ExplanationHealthEvidence | None
    delivery: DeliveryHealthEvidence | None
