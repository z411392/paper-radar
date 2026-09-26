from pathlib import Path

from libs.research_workflow.dtos.workspace_restore import WorkspaceRestoreResult
from libs.research_workflow.ports.workspace_restore_port import WorkspaceRestorePort


class RestoreWorkspace:
    def __init__(self, restore: WorkspaceRestorePort) -> None:
        self._restore = restore

    def __call__(
        self,
        *,
        backup_directory: Path,
        target: Path,
    ) -> WorkspaceRestoreResult:
        return self._restore.restore(backup_directory, target)
