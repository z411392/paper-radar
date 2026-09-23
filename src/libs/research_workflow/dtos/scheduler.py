from dataclasses import dataclass
from datetime import datetime

from libs.research_workflow.dtos.workflow_job import EnqueueWorkflowJob


@dataclass(frozen=True)
class HarvestBindingSchedule:
    binding_key: str
    profile_id: str
    profile_revision: int
    domain_id: str
    domain_revision: int
    source_id: str
    last_succeeded_window_end: datetime | None


@dataclass(frozen=True)
class DeliverySchedule:
    subscription_id: str
    timezone: str
    local_time: str
    last_scheduled_cutoff: datetime | None


@dataclass(frozen=True)
class KnownWorkflowJob:
    business_key: str
    state: str
    binding_key: str


@dataclass(frozen=True)
class CoverageGap:
    kind: str
    identity: str
    reason: str


@dataclass(frozen=True)
class SchedulerSnapshot:
    harvest_bindings: tuple[HarvestBindingSchedule, ...]
    delivery_schedules: tuple[DeliverySchedule, ...]
    known_jobs: tuple[KnownWorkflowJob, ...]
    input_gaps: tuple[CoverageGap, ...]


@dataclass(frozen=True)
class SchedulerPlan:
    jobs: tuple[EnqueueWorkflowJob, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    digest_deferred: bool


@dataclass(frozen=True)
class SchedulerTickResult:
    new_jobs: int
    replayed_jobs: int
    deferred_jobs: int
    business_keys: tuple[str, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    digest_deferred: bool
