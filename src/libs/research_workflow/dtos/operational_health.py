from dataclasses import dataclass

from libs.delivery.dtos.delivery_health import DeliveryHealthEvidence
from libs.paper_explanations.dtos.explanation_health import ExplanationHealthEvidence
from libs.research_workflow.dtos.scheduler import CoverageGap


@dataclass(frozen=True)
class WorkflowHealthEvidence:
    business_key: str
    state: str
    created_at: str
    due_at: str
    finished_at: str | None
    last_error_code: str | None


@dataclass(frozen=True)
class RuntimeHealthEvidence:
    python_implementation: str
    python_version: str
    sqlite_version: str
    sqlite_source_id: str


@dataclass(frozen=True)
class SourceOperationalHealth:
    binding_key: str
    profile_id: str
    profile_revision: int
    domain_id: str
    domain_revision: int
    source_id: str
    evidence_state: str
    latest_successful_window_end: str | None
    latest_successful_at: str | None
    latest_observed_window_end: str | None
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
    runtime: RuntimeHealthEvidence
