from typing import Protocol

from libs.kernel.dtos.workspace_info import WorkspaceInfo


class ReadWorkspaceInfoPort(Protocol):
    def __call__(self) -> WorkspaceInfo: ...
