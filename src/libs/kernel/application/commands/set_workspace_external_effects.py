from libs.kernel.dtos.workspace_info import WorkspaceInfo
from libs.kernel.ports.workspace_external_effects_port import (
    WorkspaceExternalEffectsPort,
)


class SetWorkspaceExternalEffects:
    def __init__(self, workspace: WorkspaceExternalEffectsPort) -> None:
        self._workspace = workspace

    def __call__(
        self,
        enabled: bool,
        *,
        expected_workspace_id: str | None = None,
        expected_epoch: int | None = None,
        expected_enabled: bool | None = None,
    ) -> WorkspaceInfo:
        return self._workspace.set_enabled(
            enabled,
            expected_workspace_id=expected_workspace_id,
            expected_epoch=expected_epoch,
            expected_enabled=expected_enabled,
        )
