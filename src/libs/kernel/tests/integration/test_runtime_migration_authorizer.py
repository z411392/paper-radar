"""Actual migration runner/bootstrap coverage. No executescript replacement for the runner."""

import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_migration_runner import SqliteMigrationRunner
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError

ROOT = Path(__file__).resolve().parents[5]


def history(root: Path) -> tuple[tuple[object, ...], ...]:
    connection = SqliteConnectionFactory(root).connect()
    try:
        return tuple(tuple(row) for row in connection.execute(
            "SELECT version,name,sha256,applied_at FROM schema_migrations ORDER BY version"
        ))
    finally:
        connection.close()


@pytest.mark.parametrize("version", [6, 13, 14, 15])
def test_canonical_runtime_builds_fts_without_disabling_authorizer(tmp_path: Path, version: int) -> None:
    migrations = load_workspace_migrations(with_runtime=True)[:version]
    assert len(migrations) == version
    root = tmp_path / "workspace"
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == version
    assert info.external_effects_enabled is False
    connection = SqliteSchemaConnectionFactory(root, migrations).connect()
    try:
        connection.execute(
            "INSERT INTO search_documents_fts(document_id,title) VALUES('fixture','badminton research')"
        )
        assert connection.execute(
            "SELECT document_id FROM search_documents_fts WHERE search_documents_fts MATCH 'badminton'"
        ).fetchone()[0] == "fixture"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize("sql", [
    "PRAGMA writable_schema=ON;", "PRAGMA foreign_keys=OFF;", "PRAGMA synchronous=OFF;",
    "PRAGMA user_version=999;", "PRAGMA main.data_version=42;", "PRAGMA journal_mode=MEMORY;",
    "ATTACH DATABASE ':memory:' AS other;", "DETACH DATABASE main;",
    "BEGIN;", "COMMIT;", "SAVEPOINT bypass;",
])
def test_prohibited_migration_keeps_transaction_and_existing_history(tmp_path: Path, sql: str) -> None:
    connection = sqlite3.connect(tmp_path / "failure.sqlite3", isolation_level=None)
    base = load_workspace_migrations()
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        SqliteMigrationRunner(base).apply(connection)
        original = tuple(connection.execute("SELECT * FROM schema_migrations"))
        bad = Migration(2, "0002-prohibited.sql", "CREATE TABLE must_rollback(id);" + sql)
        with pytest.raises(StorageError, match="migration_failed"):
            SqliteMigrationRunner((*base, bad)).apply(connection)
        assert tuple(connection.execute("SELECT * FROM schema_migrations")) == original
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='must_rollback'"
        ).fetchone() is None
        assert connection.in_transaction is False
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        connection.close()


def test_only_main_readonly_data_version_exception_is_permitted() -> None:
    authorize = SqliteMigrationRunner._authorize
    assert authorize(sqlite3.SQLITE_PRAGMA, "data_version", None, "main", None) == sqlite3.SQLITE_OK
    for name, value, database in (
        ("data_version", "1", "main"), ("data_version", None, "other"),
        ("writable_schema", None, "main"), ("foreign_keys", "0", "main"),
    ):
        assert authorize(sqlite3.SQLITE_PRAGMA, name, value, database, None) == sqlite3.SQLITE_DENY


@pytest.mark.parametrize("version", [1, 4, 13, 14])
def test_explicit_upgrade_preserves_identity_epoch_gate_and_old_history(tmp_path: Path, version: int) -> None:
    bundle = load_workspace_migrations(with_runtime=True)
    root = tmp_path / "workspace"
    before = SqliteWorkspaceBootstrapAdapter(root, bundle[:version]).initialize()
    connection = SqliteConnectionFactory(root).connect()
    try:
        connection.execute("UPDATE workspace_metadata SET epoch=7 WHERE singleton=1")
    finally:
        connection.close()
    prior = history(root)
    with pytest.raises(StorageError, match="schema_upgrade_required"):
        SqliteSchemaConnectionFactory(root, bundle, minimum_version=15).connect()
    assert history(root) == prior
    after = SqliteWorkspaceBootstrapAdapter(root, bundle).initialize()
    assert (after.workspace_id, after.epoch, after.external_effects_enabled, after.schema_version) == (
        before.workspace_id, 7, False, 15,
    )
    assert history(root)[:version] == prior
    assert SqliteWorkspaceBootstrapAdapter(root, bundle).initialize() == after
    assert [(row[0], row[1], row[2]) for row in history(root)] == [
        (migration.version, migration.name, migration.sha256) for migration in bundle
    ]


def test_last_migration_failure_rolls_back_claim_schema_and_can_retry(tmp_path: Path) -> None:
    bundle = load_workspace_migrations(with_runtime=True)
    root = tmp_path / "workspace"
    before = SqliteWorkspaceBootstrapAdapter(root, bundle[:13]).initialize()
    prior = history(root)
    bad = (*bundle[:14], replace(bundle[14], sql=bundle[14].sql + "\nNOT VALID SQL;"))
    with pytest.raises(StorageError, match="migration_failed"):
        SqliteWorkspaceBootstrapAdapter(root, bad).initialize()
    assert history(root) == prior
    connection = SqliteConnectionFactory(root).connect()
    try:
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE name IN ('crossref_capture_claims','crossref_capture_inbox')"
        ).fetchall() == []
    finally:
        connection.close()
    after = SqliteWorkspaceBootstrapAdapter(root, bundle).initialize()
    assert (after.workspace_id, after.epoch, after.external_effects_enabled) == (
        before.workspace_id, before.epoch, False,
    )
    assert after.schema_version == 15


def test_process_exit_during_upgrade_keeps_v13_and_reopens_safely(tmp_path: Path) -> None:
    bundle = load_workspace_migrations(with_runtime=True)
    root = tmp_path / "workspace"
    before = SqliteWorkspaceBootstrapAdapter(root, bundle[:13]).initialize()
    prior = history(root)
    script = '''
import os, sqlite3, sys
from pathlib import Path
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_migration_runner import SqliteMigrationRunner
class ExitDuringInboxCreation(SqliteMigrationRunner):
    @staticmethod
    def _authorize(action, arg1, arg2, db, source):
        if action == sqlite3.SQLITE_CREATE_TABLE and arg1 == "crossref_capture_inbox":
            os._exit(38)
        return SqliteMigrationRunner._authorize(action, arg1, arg2, db, source)
c = SqliteConnectionFactory(Path(sys.argv[1])).connect()
ExitDuringInboxCreation(load_workspace_migrations(with_runtime=True)).apply(c)
'''
    result = subprocess.run([sys.executable, "-c", script, str(root)], cwd=ROOT / "src",
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 38, result.stdout + result.stderr
    assert history(root) == prior
    after = SqliteWorkspaceBootstrapAdapter(root, bundle).initialize()
    assert after.workspace_id == before.workspace_id
    assert after.schema_version == 15 and after.external_effects_enabled is False
