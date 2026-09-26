from pathlib import Path
from typing import Protocol

from libs.research_workflow.dtos.workspace_restore import WorkspaceRestoreResult


class WorkspaceRestorePort(Protocol):
    def restore(
        self,
        backup_directory: Path,
        target: Path,
    ) -> WorkspaceRestoreResult: ...
