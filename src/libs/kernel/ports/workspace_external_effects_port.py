from typing import Protocol

from libs.kernel.dtos.workspace_info import WorkspaceInfo


class WorkspaceExternalEffectsPort(Protocol):
    def set_enabled(
        self,
        enabled: bool,
        *,
        expected_workspace_id: str | None = None,
        expected_epoch: int | None = None,
        expected_enabled: bool | None = None,
    ) -> WorkspaceInfo: ...
