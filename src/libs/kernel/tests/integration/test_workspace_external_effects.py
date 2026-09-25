import json
import subprocess
import sys
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.adapters.driven.sqlite_workspace_external_effects_adapter import (
    SqliteWorkspaceExternalEffectsAdapter,
)
from libs.kernel.exceptions.storage_error import StorageError


ROOT = Path(__file__).resolve().parents[5]


def _run(*args: str):
    return subprocess.run(
        [sys.executable, "-m", "apps.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_external_effects_toggle_is_idempotent_and_preserves_identity(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    original = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert original.schema_version == 17
    assert original.external_effects_enabled is False

    connection = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=17,
    )
    adapter = SqliteWorkspaceExternalEffectsAdapter(connection.connect)

    first = adapter.set_enabled(True)
    second = adapter.set_enabled(True)
    disabled = adapter.set_enabled(False)

    assert first.external_effects_enabled is True
    assert second == first
    assert disabled.external_effects_enabled is False
    assert disabled.workspace_id == original.workspace_id
    assert disabled.epoch == original.epoch
    assert disabled.schema_version == 17


def test_external_effects_rejects_non_boolean_state(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    connection = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=17,
    )
    adapter = SqliteWorkspaceExternalEffectsAdapter(connection.connect)

    with pytest.raises(StorageError, match="invalid_external_effects_state"):
        adapter.set_enabled(1)


def test_cli_enable_disable_is_explicit_and_machine_readable(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr

    enabled = _run(
        "effects",
        "enable",
        "--workspace",
        str(workspace),
    )
    assert enabled.returncode == 0, enabled.stdout + enabled.stderr
    enabled_info = json.loads(enabled.stdout)
    assert enabled_info["external_effects_enabled"] is True

    disabled = _run(
        "effects",
        "disable",
        "--workspace",
        str(workspace),
    )
    assert disabled.returncode == 0, disabled.stdout + disabled.stderr
    disabled_info = json.loads(disabled.stdout)
    assert disabled_info["external_effects_enabled"] is False
    assert disabled_info["workspace_id"] == enabled_info["workspace_id"]
    assert disabled_info["epoch"] == enabled_info["epoch"]


def test_effects_never_upgrades_discovery_only_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "discovery"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-discovery",
    )
    assert initialized.returncode == 0

    result = _run(
        "effects",
        "enable",
        "--workspace",
        str(workspace),
    )

    assert result.returncode == 1
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "schema_upgrade_required"
    assert "--with-runtime" in error["hint"]

    connection = SqliteSchemaConnectionFactory(
        workspace,
        load_workspace_migrations(with_runtime=True),
        minimum_version=4,
    ).connect()
    try:
        row = connection.execute(
            "SELECT external_effects_enabled FROM workspace_metadata "
            "WHERE singleton=1"
        ).fetchone()
        assert row[0] == 0
        assert connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0] == 4
    finally:
        connection.close()


def test_effects_never_creates_missing_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "missing"

    result = _run(
        "effects",
        "enable",
        "--workspace",
        str(workspace),
    )

    assert result.returncode == 1
    assert not workspace.exists()
