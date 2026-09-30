import sqlite3
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.exceptions.storage_error import StorageError


def factory(root: Path):
    from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory

    return SqliteSchemaConnectionFactory(root, load_workspace_migrations(with_profiles=True))


def test_schema_check_does_not_create_workspace(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    with pytest.raises(StorageError, match="workspace_missing"):
        factory(root).connect()
    assert not root.exists()


def test_missing_migration_is_not_applied_by_connection(tmp_path: Path) -> None:
    SqliteWorkspaceBootstrapAdapter(tmp_path, load_workspace_migrations()).initialize()
    with pytest.raises(StorageError, match="schema_upgrade_required"):
        factory(tmp_path).connect()
    with sqlite3.connect(tmp_path / "state/app.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (1,)
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='watch_profiles'").fetchall() == []
        )


def test_matching_schema_returns_no_active_transaction(tmp_path: Path) -> None:
    SqliteWorkspaceBootstrapAdapter(tmp_path, load_workspace_migrations(with_profiles=True)).initialize()
    connection = factory(tmp_path).connect()
    try:
        assert not connection.in_transaction
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_migration_hash_drift_is_not_silently_repaired(tmp_path: Path) -> None:
    SqliteWorkspaceBootstrapAdapter(tmp_path, load_workspace_migrations(with_profiles=True)).initialize()
    with sqlite3.connect(tmp_path / "state/app.sqlite3") as connection:
        connection.execute("UPDATE schema_migrations SET sha256=? WHERE version=2", ("0" * 64,))
    with pytest.raises(StorageError, match="migration_drift"):
        factory(tmp_path).connect()
    with sqlite3.connect(tmp_path / "state/app.sqlite3") as connection:
        assert (
            connection.execute("SELECT sha256 FROM schema_migrations WHERE version=2").fetchone()[0]
            == "0" * 64
        )


def test_bundle_uses_exact_canonical_sql() -> None:
    root = Path(__file__).resolve().parents[5]
    selected = load_workspace_migrations(with_profiles=True)
    assert [m.version for m in selected] == [1, 2]
    assert [m.name for m in selected] == ["0001-object-registry.sql", "0002-watch-profiles.sql"]
    for migration in selected:
        assert migration.sql.encode() == (root / "migrations" / migration.name).read_bytes()
