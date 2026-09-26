from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceRestoreResult:
    workspace_id: str
    epoch: int
    object_count: int
    state: str
