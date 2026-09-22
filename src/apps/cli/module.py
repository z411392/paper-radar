from pathlib import Path

from injector import Binder, InstanceProvider, Module, singleton

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.initialize_workspace import InitializeWorkspace
from libs.kernel.ports.initialize_workspace_port import InitializeWorkspacePort
from libs.kernel.ports.workspace_bootstrap_port import WorkspaceBootstrapPort
from libs.research_workflow.adapters.driven.python_runtime_version_adapter import (
    PythonRuntimeVersionAdapter,
)
from libs.research_workflow.application.queries.read_runtime_version import ReadRuntimeVersion
from libs.research_workflow.ports.read_runtime_version_port import ReadRuntimeVersionPort
from libs.research_workflow.ports.runtime_version_provider_port import RuntimeVersionProviderPort


class CliModule(Module):
    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = workspace

    def configure(self, binder: Binder) -> None:
        binder.bind(RuntimeVersionProviderPort, to=PythonRuntimeVersionAdapter, scope=singleton)
        binder.bind(ReadRuntimeVersionPort, to=ReadRuntimeVersion, scope=singleton)
        if self._workspace is not None:
            bootstrap = SqliteWorkspaceBootstrapAdapter(Path(self._workspace), load_workspace_migrations())
            binder.bind(WorkspaceBootstrapPort, to=InstanceProvider(bootstrap), scope=singleton)
            command = InitializeWorkspace(bootstrap)
            binder.bind(InitializeWorkspacePort, to=InstanceProvider(command), scope=singleton)
