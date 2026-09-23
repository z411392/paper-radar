from dataclasses import dataclass

from libs.research_workflow.dtos.scheduler import SchedulerTickResult


@dataclass(frozen=True)
class WorkflowJobProcessResult:
    state: str
    job_id: str | None
    job_kind: str | None
    error_code: str | None


@dataclass(frozen=True)
class WorkerCycleResult:
    scheduler: SchedulerTickResult
    processed_jobs: int
    jobs: tuple[WorkflowJobProcessResult, ...]
