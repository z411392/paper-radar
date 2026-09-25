import sqlite3
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.sqlite_workspace_info_adapter import (
    SqliteWorkspaceInfoAdapter,
)
from libs.kernel.exceptions.storage_error import StorageError


def _connect(path: Path):
    def factory():
        connection = sqlite3.connect(path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return factory


def test_workspace_info_read_is_exact_and_readonly(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE workspace_metadata("
        "singleton INTEGER PRIMARY KEY,workspace_id TEXT,epoch INTEGER,"
        "external_effects_enabled INTEGER,created_at TEXT,restored_from TEXT);"
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT,applied_at TEXT);"
        "INSERT INTO workspace_metadata VALUES(1,'workspace:test',7,0,'now',NULL);"
        "INSERT INTO schema_migrations VALUES(16,'0016','now');"
    )
    connection.commit()
    connection.close()

    info = SqliteWorkspaceInfoAdapter(_connect(path))()

    assert info.workspace_id == "workspace:test"
    assert info.epoch == 7
    assert info.external_effects_enabled is False
    assert info.schema_version == 20
    connection = sqlite3.connect(path)
    assert connection.total_changes == 0
    connection.close()


def test_workspace_info_rejects_invalid_master_gate_value(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE workspace_metadata("
        "singleton INTEGER PRIMARY KEY,workspace_id TEXT,epoch INTEGER,"
        "external_effects_enabled INTEGER,created_at TEXT,restored_from TEXT);"
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT,applied_at TEXT);"
        "INSERT INTO workspace_metadata VALUES(1,'workspace:test',7,2,'now',NULL);"
        "INSERT INTO schema_migrations VALUES(16,'0016','now');"
    )
    connection.commit()
    connection.close()

    with pytest.raises(StorageError, match="workspace_metadata_invalid"):
        SqliteWorkspaceInfoAdapter(_connect(path))()
