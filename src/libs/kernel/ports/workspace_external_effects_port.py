from typing import Protocol

from libs.kernel.dtos.workspace_info import WorkspaceInfo


class WorkspaceExternalEffectsPort(Protocol):
    def set_enabled(self, enabled: bool) -> WorkspaceInfo: ...
