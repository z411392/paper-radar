import json

import pytest

from apps.cli.adapters.driving.inspect_health import run_health_cli
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)


def test_health_cli_replays_empty_workspace_without_enabling_effects(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "runtime"
    info = SqliteWorkspaceBootstrapAdapter(
        workspace,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    assert info.schema_version == 25
    assert info.external_effects_enabled is False

    run_health_cli(["health", "--workspace", str(workspace)])
    first = json.loads(capsys.readouterr().out)
    run_health_cli(["health", "--workspace", str(workspace)])
    second = json.loads(capsys.readouterr().out)

    assert first == second
    assert first["sources"] == []
    assert first["coverage_gaps"] == []
    assert first["explanations"] == {"qa_rejected": 0, "usage_periods": []}
    assert first["delivery"] == {"unknown_deliveries": 0}
    assert first["runtime"]["sqlite_version"]
    assert first["runtime"]["sqlite_source_id"]

    connection = SqliteConnectionFactory(workspace).connect()
    try:
        assert connection.execute(
            "SELECT external_effects_enabled FROM workspace_metadata WHERE singleton=1"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_health_cli_rejects_previous_runtime_schema_as_structured_error(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(workspace, migrations[:24]).initialize()
    assert info.schema_version == 24

    with pytest.raises(SystemExit) as raised:
        run_health_cli(["health", "--workspace", str(workspace)])

    assert raised.value.code == 1
    output = json.loads(capsys.readouterr().err)
    assert output == {"error": {"code": "schema_upgrade_required"}}
