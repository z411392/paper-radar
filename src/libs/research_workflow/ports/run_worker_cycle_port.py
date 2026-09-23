from typing import Protocol

from libs.research_workflow.dtos.worker import WorkerCycleResult


class RunWorkerCyclePort(Protocol):
    def __call__(
        self,
        owner_id: str,
        *,
        max_new_jobs: int,
        max_jobs: int,
        lease_seconds: int,
    ) -> WorkerCycleResult: ...
