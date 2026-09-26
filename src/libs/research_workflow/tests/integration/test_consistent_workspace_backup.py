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
from libs.kernel.dtos.migration import Migration
from libs.research_workflow.adapters.driven.local_workspace_backup_adapter import (
    LocalWorkspaceBackupAdapter,
)
from libs.research_workflow.application.commands.backup_workspace import BackupWorkspace
from libs.research_workflow.exceptions.workspace_backup_error import WorkspaceBackupError


ROOT = Path(__file__).resolve().parents[5]
NOW = datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)
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


def _workspace(tmp_path: Path) -> tuple[Path, SqliteConnectionFactory]:
    root = tmp_path / "workspace"
    info = SqliteWorkspaceBootstrapAdapter(root, _migrations()).initialize()
    assert info.schema_version == 8
    return root, SqliteConnectionFactory(root)


def _publish(
    root: Path,
    content: bytes,
    *,
    kind: str = "raw",
    media_type: str = "application/octet-stream",
):
    factory = SqliteConnectionFactory(root)
    return PublishObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(factory),
    )(content, kind, media_type, "retain")


def _backup(root: Path, run_id: str):
    return BackupWorkspace(LocalWorkspaceBackupAdapter(root))(
        run_id=run_id,
        created_at=NOW,
    )


def _manifest(root: Path, relative_directory: str) -> dict[str, object]:
    path = root / relative_directory / "manifest.json"
    return json.loads(path.read_text())


def _run_row(factory: SqliteConnectionFactory, run_id: str) -> sqlite3.Row:
    connection = factory.connect()
    try:
        row = connection.execute(
            "SELECT * FROM backup_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        assert row is not None
        return row
    finally:
        connection.close()


def test_backup_uses_sqlite_snapshot_and_captures_wal_commit(tmp_path: Path) -> None:
    root, factory = _workspace(tmp_path)
    writer = factory.connect()
    writer.execute(
        "INSERT INTO workflow_events VALUES(?,?,?,?,?,?,?)",
        (
            "event:before-backup",
            "fixture",
            "source:one",
            "fixture_created",
            "fixture:one",
            "{}",
            None,
            NOW.isoformat(),
        ),
    )
    assert (root / "state/app.sqlite3-wal").exists()

    try:
        result = _backup(root, "backup:wal")
    finally:
        writer.close()

    snapshot = sqlite3.connect(root / result.relative_directory / "state/app.sqlite3")
    try:
        assert snapshot.execute(
            "SELECT id FROM workflow_events WHERE id='event:before-backup'"
        ).fetchone() == ("event:before-backup",)
        assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        snapshot.close()
    assert result.state == "verified"


def test_backup_copies_every_registered_object_and_hashes_manifest(tmp_path: Path) -> None:
    root, factory = _workspace(tmp_path)
    first = _publish(root, b"raw-one", kind="raw", media_type="text/plain")
    second = _publish(root, b"vector-two", kind="embedding")

    result = _backup(root, "backup:closure")
    manifest = _manifest(root, result.relative_directory)

    assert result.object_count == 2
    assert manifest["state"] == "verified"
    assert manifest["object_count"] == 2
    assert [item["object_id"] for item in manifest["objects"]] == sorted(
        [first.object_id, second.object_id]
    )
    assert (root / result.relative_directory / first.relative_path).read_bytes() == b"raw-one"
    assert (
        root / result.relative_directory / second.relative_path
    ).read_bytes() == b"vector-two"

    row = _run_row(factory, "backup:closure")
    assert row["state"] == "verified"
    assert row["database_sha256"] == result.database_sha256
    assert row["manifest_sha256"] == result.manifest_sha256
    assert row["object_count"] == 2
    assert row["verified_at"] is not None


@pytest.mark.parametrize(
    ("mode", "error_code"),
    [
        ("missing", "backup_object_missing"),
        ("corrupt", "backup_object_corrupt"),
    ],
)
def test_missing_or_corrupt_registered_object_never_verifies_backup(
    tmp_path: Path,
    mode: str,
    error_code: str,
) -> None:
    root, factory = _workspace(tmp_path)
    ref = _publish(root, b"registered")
    object_path = root / ref.relative_path
    if mode == "missing":
        object_path.unlink()
    else:
        object_path.write_bytes(b"REGISTERED")

    with pytest.raises(WorkspaceBackupError, match=error_code):
        _backup(root, f"backup:{mode}")

    row = _run_row(factory, f"backup:{mode}")
    assert row["state"] == "failed"
    backup_dir = root / row["relative_directory"]
    assert not (backup_dir / "manifest.json").exists()
    partial = json.loads((backup_dir / "manifest.partial.json").read_text())
    assert partial["state"] == "building"


def test_unregistered_orphan_is_not_part_of_snapshot_reference_closure(
    tmp_path: Path,
) -> None:
    root, _ = _workspace(tmp_path)
    registered = _publish(root, b"registered")
    orphan = FilesystemObjectBytesAdapter(root).publish(
        b"orphan",
        "raw",
        "application/octet-stream",
        "retain",
    )

    result = _backup(root, "backup:orphan")
    manifest = _manifest(root, result.relative_directory)

    assert [item["object_id"] for item in manifest["objects"]] == [
        registered.object_id
    ]
    assert not (root / result.relative_directory / orphan.relative_path).exists()


def test_object_closure_is_read_from_snapshot_not_live_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _workspace(tmp_path)
    first = _publish(root, b"before")
    original = LocalWorkspaceBackupAdapter._snapshot_database

    def snapshot_then_publish(self, backup_directory):
        result = original(self, backup_directory)
        _publish(root, b"after")
        return result

    monkeypatch.setattr(
        LocalWorkspaceBackupAdapter,
        "_snapshot_database",
        snapshot_then_publish,
    )

    result = _backup(root, "backup:snapshot")
    manifest = _manifest(root, result.relative_directory)

    assert [item["object_id"] for item in manifest["objects"]] == [
        first.object_id
    ]
    connection = SqliteConnectionFactory(root).connect()
    try:
        assert connection.execute("SELECT count(*) FROM object_registry").fetchone()[0] == 2
    finally:
        connection.close()


def test_exact_pin_exists_while_registered_objects_are_copied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _workspace(tmp_path)
    ref = _publish(root, b"pinned")
    original = LocalWorkspaceBackupAdapter._copy_object

    def inspect_pin(self, object_ref, backup_directory):
        pin_path = root / "state/backup-pins/backup:pin.json"
        payload = json.loads(pin_path.read_text())
        assert payload["state"] == "building"
        assert payload["object_ids"] == [ref.object_id]
        return original(self, object_ref, backup_directory)

    monkeypatch.setattr(
        LocalWorkspaceBackupAdapter,
        "_copy_object",
        inspect_pin,
    )

    _backup(root, "backup:pin")

    assert not (root / "state/backup-pins/backup:pin.json").exists()


@pytest.mark.parametrize(
    "run_id",
    ["", ".", "..", "../escape", "backup/escape", "backup\\escape", "\x00bad"],
)
def test_unsafe_backup_identity_is_rejected_before_any_write(
    tmp_path: Path,
    run_id: str,
) -> None:
    root, factory = _workspace(tmp_path)

    with pytest.raises(WorkspaceBackupError, match="invalid_backup_id"):
        _backup(root, run_id)

    connection = factory.connect()
    try:
        assert connection.execute("SELECT count(*) FROM backup_runs").fetchone()[0] == 0
    finally:
        connection.close()
