import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_migration_runner import SqliteMigrationRunner
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.initialize_workspace import InitializeWorkspace
from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError

ROOT = Path(__file__).resolve().parents[5]
MIGRATION = Migration(
    1, "0001-object-registry.sql", (ROOT / "migrations/0001-object-registry.sql").read_text()
)


def components(root: Path):
    info = InitializeWorkspace(SqliteWorkspaceBootstrapAdapter(root, (MIGRATION,)))()
    return info, SqliteConnectionFactory(root, busy_timeout_ms=50)


def test_workspace_reopens_with_stable_identity_and_pragmas(tmp_path):
    root = tmp_path / "workspace"
    info, factory = components(root)
    assert components(root)[0] == info
    assert not info.external_effects_enabled
    assert info.epoch == 1
    with factory.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 50
        assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()


def test_foreign_database_is_not_adopted_or_rewritten(tmp_path):
    root = tmp_path / "workspace"
    (root / "state").mkdir(parents=True)
    dbpath = root / "state/app.sqlite3"
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE unrelated (value TEXT)")
    db.close()
    before = dbpath.read_bytes()
    with pytest.raises(StorageError, match="foreign_database"):
        components(root)
    assert dbpath.read_bytes() == before


def test_missing_database_is_not_created_by_regular_connect(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    with pytest.raises(StorageError):
        SqliteConnectionFactory(root).connect()
    assert list(root.iterdir()) == []


def test_migration_hash_drift_rejected(tmp_path):
    root = tmp_path / "workspace"
    info, _ = components(root)
    changed = replace(MIGRATION, sql=MIGRATION.sql + "\n-- changed\n")
    with pytest.raises(StorageError, match="migration_drift"):
        SqliteWorkspaceBootstrapAdapter(root, (changed,)).initialize()
    assert components(root)[0] == info


def test_failed_migration_rolls_back_ddl_and_receipt(tmp_path):
    _, factory = components(tmp_path / "workspace")
    bad = Migration(2, "0002-test.sql", "CREATE TABLE transient (x INTEGER); INVALID SQL;")
    db = factory.connect()
    with pytest.raises(StorageError, match="migration_failed"):
        SqliteMigrationRunner((MIGRATION, bad)).apply(db)
    assert not db.in_transaction
    assert db.execute("SELECT name FROM sqlite_master WHERE name='transient'").fetchone() is None
    assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize(
    "statement", ["COMMIT;", 'ATTACH DATABASE ":memory:" AS alien;', "PRAGMA user_version=9;"]
)
def test_migration_cannot_escape_transaction(tmp_path, statement):
    _, factory = components(tmp_path / "workspace")
    bad = Migration(2, "0002-test.sql", "CREATE TABLE transient (x INTEGER);" + statement)
    db = factory.connect()
    with pytest.raises(StorageError, match="migration_failed"):
        SqliteMigrationRunner((MIGRATION, bad)).apply(db)
    assert db.execute("SELECT name FROM sqlite_master WHERE name='transient'").fetchone() is None
    db.close()


def test_migration_handles_trigger_and_quoted_semicolons(tmp_path):
    _, factory = components(tmp_path / "workspace")
    upgrade = Migration(
        2,
        "0002-test.sql",
        """CREATE TABLE x (value TEXT);
    CREATE TABLE y (value TEXT);
    CREATE TRIGGER record_x AFTER INSERT ON x BEGIN
      INSERT INTO y VALUES('a;b');
      INSERT INTO y VALUES(NEW.value);
    END;
    INSERT INTO x VALUES('ok');""",
    )
    db = factory.connect()
    SqliteMigrationRunner((MIGRATION, upgrade)).apply(db)
    assert [r[0] for r in db.execute("SELECT value FROM y")] == ["a;b", "ok"]
    assert SqliteMigrationRunner((MIGRATION, upgrade)).apply(db) == 2
    db.close()


@pytest.mark.parametrize("part", ["state", "objects", "tmp"])
def test_symlink_workspace_components_rejected(tmp_path, part):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / part).symlink_to(outside, target_is_directory=True)
    with pytest.raises(StorageError, match="unsafe_path"):
        components(root)
    assert list(outside.iterdir()) == []


def test_root_symlink_and_parent_traversal_are_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    for root in [alias, tmp_path / "unused/../target"]:
        with pytest.raises(StorageError, match="unsafe_path"):
            components(root)
    assert list(target.iterdir()) == []


def test_symlink_database_and_sidecar_rejected(tmp_path):
    root = tmp_path / "workspace"
    components(root)
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"keep")
    sidecar = root / "state/app.sqlite3-journal"
    sidecar.symlink_to(foreign)
    with pytest.raises(StorageError, match="unsafe_path"):
        SqliteConnectionFactory(root).connect()
    assert foreign.read_bytes() == b"keep"


def test_concurrent_initialization_returns_the_winning_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    root = tmp_path / "workspace"
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: InitializeWorkspace(SqliteWorkspaceBootstrapAdapter(root, (MIGRATION,)))(), range(8)
            )
        )
    assert len(set(results)) == 1


def test_missing_db_with_remaining_objects_is_not_reinitialized(tmp_path):
    root = tmp_path / "workspace"
    (root / "objects/raw").mkdir(parents=True)
    valuable = root / "objects/raw/existing"
    valuable.write_bytes(b"irreplaceable")
    with pytest.raises(StorageError, match="foreign_workspace"):
        components(root)
    assert valuable.read_bytes() == b"irreplaceable"
    assert not (root / "state/app.sqlite3").exists()


def test_foreign_state_file_is_not_absorbed(tmp_path):
    root = tmp_path / "workspace"
    (root / "state").mkdir(parents=True)
    valuable = root / "state/other.sqlite3"
    valuable.write_bytes(b"do not touch")
    with pytest.raises(StorageError, match="foreign_workspace"):
        components(root)
    assert valuable.read_bytes() == b"do not touch"
