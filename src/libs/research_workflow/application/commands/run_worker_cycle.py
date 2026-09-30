from libs.research_workflow.dtos.worker import WorkerCycleResult
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError
from libs.research_workflow.ports.process_workflow_job_port import ProcessWorkflowJobPort
from libs.research_workflow.ports.run_scheduler_tick_port import RunSchedulerTickPort
from libs.research_workflow.ports.workflow_clock_port import WorkflowClockPort


class RunWorkerCycle:
    def __init__(
        self,
        scheduler: RunSchedulerTickPort,
        processor: ProcessWorkflowJobPort,
        clock: WorkflowClockPort,
    ) -> None:
        self._scheduler = scheduler
        self._processor = processor
        self._clock = clock

    def __call__(
        self,
        owner_id: str,
        *,
        max_new_jobs: int,
        max_jobs: int,
        lease_seconds: int,
    ) -> WorkerCycleResult:
        if type(max_jobs) is not int or not 1 <= max_jobs <= 1000:
            raise WorkflowJobError("invalid_worker_job_limit")
        scheduler = self._scheduler(
            now=self._clock.now(),
            max_new_jobs=max_new_jobs,
        )
        outcomes = []
        for _ in range(max_jobs):
            outcome = self._processor(owner_id, lease_seconds=lease_seconds)
            if outcome.state == "idle":
                break
            outcomes.append(outcome)
        return WorkerCycleResult(
            scheduler=scheduler,
            processed_jobs=len(outcomes),
            jobs=tuple(outcomes),
        )
