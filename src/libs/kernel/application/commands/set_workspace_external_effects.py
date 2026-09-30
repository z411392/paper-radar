from libs.kernel.dtos.workspace_info import WorkspaceInfo
from libs.kernel.ports.workspace_external_effects_port import (
    WorkspaceExternalEffectsPort,
)


class SetWorkspaceExternalEffects:
    def __init__(self, workspace: WorkspaceExternalEffectsPort) -> None:
        self._workspace = workspace

    def __call__(self, enabled: bool) -> WorkspaceInfo:
        return self._workspace.set_enabled(enabled)
