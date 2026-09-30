from libs.kernel.dtos.workspace_info import WorkspaceInfo
from libs.kernel.ports.workspace_bootstrap_port import WorkspaceBootstrapPort


class InitializeWorkspace:
    def __init__(self, bootstrap: WorkspaceBootstrapPort) -> None:
        self._bootstrap = bootstrap

    def __call__(self) -> WorkspaceInfo:
        return self._bootstrap.initialize()
