from datetime import datetime

from libs.research_workflow.domain.services.plan_catchup_jobs import PlanCatchupJobs
from libs.research_workflow.dtos.scheduler import SchedulerTickResult
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError
from libs.research_workflow.ports.scheduler_input_port import SchedulerInputPort
from libs.research_workflow.ports.workflow_job_store_port import WorkflowJobStorePort


class RunSchedulerTick:
    def __init__(
        self,
        inputs: SchedulerInputPort,
        jobs: WorkflowJobStorePort,
    ) -> None:
        self._inputs = inputs
        self._jobs = jobs
        self._planner = PlanCatchupJobs()

    def __call__(
        self,
        *,
        now: datetime,
        max_new_jobs: int = 20,
    ) -> SchedulerTickResult:
        if type(max_new_jobs) is not int or not 1 <= max_new_jobs <= 1000:
            raise WorkflowJobError("invalid_scheduler_job_limit")
        snapshot = self._inputs.read(now)
        plan = self._planner(snapshot, now=now)
        new_jobs = 0
        replayed = 0
        keys: list[str] = []
        deferred = 0
        for index, job in enumerate(plan.jobs):
            if new_jobs >= max_new_jobs:
                deferred = len(plan.jobs) - index
                break
            outcome = self._jobs.enqueue(job)
            keys.append(job.business_key)
            if outcome.replayed:
                replayed += 1
            else:
                new_jobs += 1
        return SchedulerTickResult(
            new_jobs=new_jobs,
            replayed_jobs=replayed,
            deferred_jobs=deferred,
            business_keys=tuple(keys),
            coverage_gaps=plan.coverage_gaps,
            digest_deferred=plan.digest_deferred,
        )
