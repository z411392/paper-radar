import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import (
    FilesystemObjectBytesAdapter,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.kernel.dtos.migration import Migration
from libs.research_workflow.adapters.driven.local_workspace_backup_adapter import (
    LocalWorkspaceBackupAdapter,
    LocalWorkspaceRestoreAdapter,
)
from libs.research_workflow.application.commands.backup_workspace import BackupWorkspace
from libs.research_workflow.application.commands.restore_workspace import RestoreWorkspace
from libs.research_workflow.exceptions.workspace_restore_error import WorkspaceRestoreError


ROOT = Path(__file__).resolve().parents[5]
NOW = datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)
MIGRATION_NAMES = (
    "0001-object-registry.sql",
    "0002-watch-profiles.sql",
    "0003-scholarly-catalog.sql",
    "0004-discovery.sql",
    "0005-paper-explanations.sql",
    "0006-retrieval.sql",
    "0007-delivery.sql",
    "0008-workflow-jobs.sql",
)


def _migrations() -> tuple[Migration, ...]:
    return tuple(
        Migration(index, name, (ROOT / "migrations" / name).read_text())
        for index, name in enumerate(MIGRATION_NAMES, start=1)
    )


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    info = SqliteWorkspaceBootstrapAdapter(root, _migrations()).initialize()
    assert info.schema_version == 8
    return root


def _publish(
    root: Path,
    content: bytes,
    *,
    kind: str,
):
    return PublishObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(SqliteConnectionFactory(root)),
    )(content, kind, "application/octet-stream", "retain")


def _backup(root: Path):
    return BackupWorkspace(LocalWorkspaceBackupAdapter(root))(
        run_id="backup:restore-fixture",
        created_at=NOW,
    )


def _restore(backup_directory: Path, target: Path):
    return RestoreWorkspace(LocalWorkspaceRestoreAdapter(_migrations()))(
        backup_directory=backup_directory,
        target=target,
    )


def _read_manifest(backup_directory: Path) -> dict[str, object]:
    return json.loads((backup_directory / "manifest.json").read_text())


def _write_manifest(
    backup_directory: Path,
    payload: dict[str, object],
) -> None:
    data = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    (backup_directory / "manifest.json").write_text(data)


def _database_hash(backup_directory: Path) -> str:
    return hashlib.sha256(
        (backup_directory / "state/app.sqlite3").read_bytes()
    ).hexdigest()


def test_restore_to_new_workspace_preserves_registered_objects(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    raw = _publish(source, b"paper bytes", kind="raw")
    vector = _publish(source, b"vector bytes", kind="embedding")
    backup = _backup(source)
    target = tmp_path / "restored"

    result = _restore(source / backup.relative_directory, target)

    assert result.state == "restored"
    assert result.object_count == 2
    assert target.is_dir()
    restored_uow = SqliteObjectUnitOfWorkAdapter(
        SqliteConnectionFactory(target)
    )
    restored_files = FilesystemObjectBytesAdapter(target)
    read = ReadObject(restored_files, restored_uow)
    assert read(raw.object_id) == b"paper bytes"
    assert read(vector.object_id) == b"vector bytes"

    source_connection = SqliteConnectionFactory(source).connect()
    restored_connection = SqliteConnectionFactory(target).connect()
    try:
        source_identity = source_connection.execute(
            "SELECT workspace_id,epoch,external_effects_enabled "
            "FROM workspace_metadata WHERE singleton=1"
        ).fetchone()
        restored_identity = restored_connection.execute(
            "SELECT workspace_id,epoch,external_effects_enabled "
            "FROM workspace_metadata WHERE singleton=1"
        ).fetchone()
        assert tuple(restored_identity) == tuple(source_identity)
        assert restored_connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []
    finally:
        source_connection.close()
        restored_connection.close()


def test_existing_empty_target_is_allowed_but_nonempty_target_is_not(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    backup = _backup(source)
    backup_directory = source / backup.relative_directory

    empty = tmp_path / "empty-target"
    empty.mkdir()
    assert _restore(backup_directory, empty).state == "restored"

    nonempty = tmp_path / "nonempty-target"
    nonempty.mkdir()
    sentinel = nonempty / "keep.txt"
    sentinel.write_text("do not overwrite")

    with pytest.raises(
        WorkspaceRestoreError,
        match="restore_target_not_empty",
    ):
        _restore(backup_directory, nonempty)

    assert sentinel.read_text() == "do not overwrite"


def test_symlink_target_and_source_target_overlap_are_rejected(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    backup = _backup(source)
    backup_directory = source / backup.relative_directory

    real_target = tmp_path / "real-target"
    real_target.mkdir()
    symlink_target = tmp_path / "symlink-target"
    symlink_target.symlink_to(real_target, target_is_directory=True)

    with pytest.raises(
        WorkspaceRestoreError,
        match="restore_target_unsafe",
    ):
        _restore(backup_directory, symlink_target)

    with pytest.raises(
        WorkspaceRestoreError,
        match="restore_path_overlap",
    ):
        _restore(backup_directory, backup_directory / "nested-workspace")


@pytest.mark.parametrize(
    ("mode", "error_code"),
    [
        ("missing", "restore_object_missing"),
        ("corrupt", "restore_object_corrupt"),
    ],
)
def test_missing_or_corrupt_backup_object_never_publishes_target(
    tmp_path: Path,
    mode: str,
    error_code: str,
) -> None:
    source = _source(tmp_path)
    ref = _publish(source, b"restore me", kind="raw")
    backup = _backup(source)
    backup_directory = source / backup.relative_directory
    object_path = backup_directory / ref.relative_path
    if mode == "missing":
        object_path.unlink()
    else:
        object_path.write_bytes(b"RESTORE ME")
    target = tmp_path / "restored"

    with pytest.raises(WorkspaceRestoreError, match=error_code):
        _restore(backup_directory, target)

    assert not target.exists()
    assert list(tmp_path.glob(".restored.restore-*")) == []


def test_manifest_object_list_must_exactly_match_snapshot_registry(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    _publish(source, b"one", kind="raw")
    _publish(source, b"two", kind="embedding")
    backup = _backup(source)
    backup_directory = source / backup.relative_directory
    manifest = _read_manifest(backup_directory)
    objects = manifest["objects"]
    assert isinstance(objects, list)
    manifest["objects"] = objects[:-1]
    manifest["object_count"] = len(objects) - 1
    _write_manifest(backup_directory, manifest)

    with pytest.raises(
        WorkspaceRestoreError,
        match="restore_manifest_mismatch",
    ):
        _restore(backup_directory, tmp_path / "restored")


def test_database_hash_mismatch_is_rejected_before_target_publication(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    backup = _backup(source)
    backup_directory = source / backup.relative_directory
    database = backup_directory / "state/app.sqlite3"
    data = bytearray(database.read_bytes())
    data[-1] ^= 0x01
    database.write_bytes(data)

    with pytest.raises(
        WorkspaceRestoreError,
        match="restore_database_corrupt",
    ):
        _restore(backup_directory, tmp_path / "restored")

    assert not (tmp_path / "restored").exists()


def test_foreign_key_check_is_required_separately_from_integrity_check(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    backup = _backup(source)
    backup_directory = source / backup.relative_directory
    database = backup_directory / "state/app.sqlite3"

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO job_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "attempt:orphan",
                "job:missing",
                1,
                1,
                "failed",
                "fixture",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.commit()
        assert connection.execute("PRAGMA integrity_check").fetchone() == (
            "ok",
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()

    manifest = _read_manifest(backup_directory)
    database_spec = manifest["database"]
    assert isinstance(database_spec, dict)
    database_spec["sha256"] = _database_hash(backup_directory)
    _write_manifest(backup_directory, manifest)

    with pytest.raises(
        WorkspaceRestoreError,
        match="restore_foreign_key_violation",
    ):
        _restore(backup_directory, tmp_path / "restored")


def test_migration_drift_is_rejected_even_when_database_hash_matches(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    backup = _backup(source)
    backup_directory = source / backup.relative_directory
    database = backup_directory / "state/app.sqlite3"

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE schema_migrations SET sha256=? WHERE version=1",
            ("0" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    manifest = _read_manifest(backup_directory)
    database_spec = manifest["database"]
    assert isinstance(database_spec, dict)
    database_spec["sha256"] = _database_hash(backup_directory)
    _write_manifest(backup_directory, manifest)

    with pytest.raises(
        WorkspaceRestoreError,
        match="restore_migration_drift",
    ):
        _restore(backup_directory, tmp_path / "restored")
