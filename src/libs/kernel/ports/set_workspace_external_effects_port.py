from typing import Protocol

from libs.kernel.dtos.workspace_info import WorkspaceInfo


class SetWorkspaceExternalEffectsPort(Protocol):
    def __call__(
        self,
        enabled: bool,
        *,
        expected_workspace_id: str | None = None,
        expected_epoch: int | None = None,
        expected_enabled: bool | None = None,
    ) -> WorkspaceInfo: ...
