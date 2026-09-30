from injector import Injector

from apps.cli.module import CliModule
from libs.kernel.ports.initialize_workspace_port import InitializeWorkspacePort
from libs.kernel.ports.workspace_bootstrap_port import WorkspaceBootstrapPort


def test_workspace_binding_does_not_initialize_until_explicit_call(tmp_path):
    workspace = tmp_path / "workspace"
    injector = Injector([CliModule(str(workspace))], auto_bind=False)
    command = injector.get(InitializeWorkspacePort)
    assert callable(command)
    assert injector.get(WorkspaceBootstrapPort) is not None
    assert not workspace.exists()
    first = command()
    second = command()
    assert first == second
    assert first.external_effects_enabled is False
