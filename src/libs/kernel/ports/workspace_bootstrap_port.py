from typing import Protocol

from libs.kernel.dtos.workspace_info import WorkspaceInfo


class WorkspaceBootstrapPort(Protocol):
    def initialize(self) -> WorkspaceInfo: ...
