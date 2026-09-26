from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceRestoreResult:
    workspace_id: str
    epoch: int
    object_count: int
    state: str
    previous_epoch: int
    external_effects_enabled: bool
    source_backup_run_id: str
    reconciliation_outbox_ids: tuple[str, ...]
