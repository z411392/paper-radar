from datetime import datetime
from typing import Protocol

from libs.research_workflow.dtos.workspace_backup import WorkspaceBackupResult


class WorkspaceBackupPort(Protocol):
    def backup(
        self,
        run_id: str,
        created_at: datetime,
    ) -> WorkspaceBackupResult: ...
