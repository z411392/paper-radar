from datetime import datetime
from typing import Protocol

from libs.research_workflow.dtos.workflow_job import (
    CompleteWorkflowJob,
    EnqueuedWorkflowJob,
    EnqueueWorkflowJob,
    WorkflowJobCompletion,
    WorkflowJobLease,
)


class WorkflowJobStorePort(Protocol):
    def enqueue(self, request: EnqueueWorkflowJob) -> EnqueuedWorkflowJob: ...

    def claim_due(
        self,
        owner_id: str,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> WorkflowJobLease | None: ...

    def complete(self, request: CompleteWorkflowJob) -> WorkflowJobCompletion: ...
