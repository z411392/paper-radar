from datetime import datetime

from libs.research_workflow.dtos.workspace_backup import WorkspaceBackupResult
from libs.research_workflow.ports.workspace_backup_port import WorkspaceBackupPort


class BackupWorkspace:
    def __init__(self, backup: WorkspaceBackupPort) -> None:
        self._backup = backup

    def __call__(
        self,
        *,
        run_id: str,
        created_at: datetime,
    ) -> WorkspaceBackupResult:
        return self._backup.backup(run_id, created_at)
