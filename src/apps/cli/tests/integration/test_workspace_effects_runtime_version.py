import pytest
from injector import Injector

from apps.cli.module import WorkspaceEffectsCliModule
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.set_workspace_external_effects_port import (
    SetWorkspaceExternalEffectsPort,
)


def test_effects_enable_rejects_previous_runtime_schema(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(
        workspace,
        migrations[:23],
    ).initialize()
    assert info.schema_version == 23
    assert info.external_effects_enabled is False

    injector = Injector(
        [WorkspaceEffectsCliModule(str(workspace))],
        auto_bind=False,
    )

    with pytest.raises(StorageError, match="schema_upgrade_required"):
        injector.get(SetWorkspaceExternalEffectsPort)(True)

    connection = SqliteConnectionFactory(workspace).connect()
    try:
        enabled = connection.execute(
            "SELECT external_effects_enabled "
            "FROM workspace_metadata WHERE singleton=1"
        ).fetchone()[0]
    finally:
        connection.close()
    assert enabled == 0
