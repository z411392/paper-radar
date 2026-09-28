import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest


def call_init(cwd: Path, workspace: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", "init", "--workspace", workspace, *extra],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def assert_storage_failure(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"]
    assert "Traceback" not in result.stderr


def test_cli_initializes_and_reopens_the_same_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "研究 資料"
    first = call_init(tmp_path, str(workspace))
    assert first.returncode == 0, first.stderr
    assert first.stderr == ""
    info = json.loads(first.stdout)
    assert str(UUID(info["workspace_id"])) == info["workspace_id"]
    assert info == {
        "workspace_id": info["workspace_id"],
        "epoch": 1,
        "external_effects_enabled": False,
        "schema_version": 1,
    }
    second = call_init(tmp_path, str(workspace))
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == info
    database = workspace / "state" / "app.sqlite3"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM workspace_metadata").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM object_registry").fetchone() == (0,)
        assert connection.execute("SELECT version,name FROM schema_migrations").fetchall() == [
            (1, "0001-object-registry.sql")
        ]
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='watch_profiles'").fetchall() == []
        )


def test_cli_env_file_initializes_current_runtime_schema(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime from env"
    env_file = tmp_path / "worker.env"
    env_file.write_text(
        f"PAPER_RADAR_WORKSPACE={workspace}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "apps.cli",
            "init",
            "--env-file",
            str(env_file),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    info = json.loads(result.stdout)
    assert info["schema_version"] == 24
    assert info["external_effects_enabled"] is False
    assert (workspace / "state" / "app.sqlite3").is_file()


def test_cli_resolves_relative_path_from_invocation_directory(tmp_path: Path) -> None:
    result = call_init(tmp_path, "relative workspace")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "relative workspace" / "state" / "app.sqlite3").is_file()


@pytest.mark.parametrize("extra", [("--force",), ("unexpected",)])
def test_cli_rejects_extra_arguments_before_any_write(tmp_path: Path, extra: tuple[str, ...]) -> None:
    workspace = tmp_path / "not-created"
    result = call_init(tmp_path, str(workspace), *extra)
    assert result.returncode == 2
    assert result.stdout == ""
    assert not workspace.exists()


def test_cli_requires_explicit_workspace_before_any_write(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", "init"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_cli_empty_workspace_is_not_current_directory(tmp_path: Path) -> None:
    result = call_init(tmp_path, "")
    assert_storage_failure(result)
    assert result.returncode == 2
    assert list(tmp_path.iterdir()) == []


def test_cli_preserves_foreign_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "foreign"
    workspace.mkdir()
    original = workspace / "important.txt"
    original.write_bytes(b"preserve this unrelated file")
    result = call_init(tmp_path, str(workspace))
    assert_storage_failure(result)
    assert original.read_bytes() == b"preserve this unrelated file"
    assert not (workspace / "state" / "app.sqlite3").exists()


def test_cli_rejects_foreign_database_without_replacing_it(tmp_path: Path) -> None:
    workspace = tmp_path / "foreign-db"
    (workspace / "state").mkdir(parents=True)
    database = workspace / "state" / "app.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES('keep')")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    result = call_init(tmp_path, str(workspace))
    assert_storage_failure(result)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_cli_rejects_symlink_workspace_without_touching_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "link"
    workspace.symlink_to(outside, target_is_directory=True)
    result = call_init(tmp_path, str(workspace))
    assert_storage_failure(result)
    assert list(outside.iterdir()) == []


def test_cli_rejects_missing_database_with_preserved_content(tmp_path: Path) -> None:
    workspace = tmp_path / "interrupted"
    (workspace / "objects").mkdir(parents=True)
    original = workspace / "objects" / "saved-bytes"
    original.write_bytes(b"not expendable")
    result = call_init(tmp_path, str(workspace))
    assert_storage_failure(result)
    assert original.read_bytes() == b"not expendable"
    assert not (workspace / "state" / "app.sqlite3").exists()


def test_cli_init_help_has_no_side_effects(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", "init", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--workspace" in result.stdout
    assert list(tmp_path.iterdir()) == []
