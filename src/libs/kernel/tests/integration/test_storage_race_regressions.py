import sqlite3
from pathlib import Path

import pytest

import libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter as bootstrap_module
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError


def test_optional_sidecar_can_disappear_after_type_observation(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    database = tmp_path / "state/app.sqlite3"
    database.touch()
    sidecar = tmp_path / "state/app.sqlite3-shm"
    sidecar.write_bytes(b"temporary")
    paths = WorkspacePaths(tmp_path)
    # Isolate classification from ancestor checks and fix the exact interleaving.
    monkeypatch.setattr(paths, "path", lambda relative: tmp_path / relative)
    real_stat = Path.stat
    observed = []

    def observe_then_remove(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == sidecar and not observed:
            observed.append(True)
            sidecar.unlink()
        return result

    monkeypatch.setattr(Path, "stat", observe_then_remove)
    assert paths.database() == database
    assert observed == [True]
    assert not sidecar.exists()


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_sidecar_type_check_still_rejects_unsafe_entries(tmp_path, kind):
    (tmp_path / "state").mkdir()
    sidecar = tmp_path / "state/app.sqlite3-shm"
    if kind == "directory":
        sidecar.mkdir()
    else:
        sidecar.symlink_to(tmp_path / "not-a-real-target")
    with pytest.raises(StorageError, match="unsafe_path"):
        WorkspacePaths(tmp_path).database()


def test_new_database_has_wal_mode_before_publication(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[5]
    name = "0001-object-registry.sql"
    migration = Migration(1, name, (root / "migrations" / name).read_text(encoding="utf-8"))
    real_link = bootstrap_module.os.link
    inspected = []

    def inspect_before_link(source, destination, *args, **kwargs):
        connection = sqlite3.connect(Path(source).as_uri() + "?mode=ro", uri=True)
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert connection.execute("SELECT COUNT(*) FROM workspace_metadata").fetchone() == (1,)
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        finally:
            connection.close()
        inspected.append(True)
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(bootstrap_module.os, "link", inspect_before_link)
    info = SqliteWorkspaceBootstrapAdapter(tmp_path / "workspace", (migration,)).initialize()
    assert inspected == [True]
    assert info.schema_version == 1
    assert info.external_effects_enabled is False
