from typing import Protocol

from libs.kernel.dtos.workspace_info import WorkspaceInfo


class SetWorkspaceExternalEffectsPort(Protocol):
    def __call__(self, enabled: bool) -> WorkspaceInfo: ...
