from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceInfo:
    workspace_id: str
    epoch: int
    external_effects_enabled: bool
    schema_version: int
