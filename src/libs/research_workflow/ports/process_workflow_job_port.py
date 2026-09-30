from typing import Protocol

from libs.research_workflow.dtos.worker import WorkflowJobProcessResult


class ProcessWorkflowJobPort(Protocol):
    def __call__(
        self,
        owner_id: str,
        *,
        lease_seconds: int,
    ) -> WorkflowJobProcessResult: ...
