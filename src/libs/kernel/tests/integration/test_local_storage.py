import hashlib
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.initialize_workspace import InitializeWorkspace
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.inspect_storage import InspectStorage
from libs.kernel.application.queries.read_object import ReadObject
from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError

ROOT = Path(__file__).resolve().parents[5]
MIGRATION = Migration(
    1, "0001-object-registry.sql", (ROOT / "migrations/0001-object-registry.sql").read_text()
)


def components(root: Path):
    bootstrap = SqliteWorkspaceBootstrapAdapter(root, (MIGRATION,))
    info = InitializeWorkspace(bootstrap)()
    factory = SqliteConnectionFactory(root, busy_timeout_ms=50)
    uow = SqliteObjectUnitOfWorkAdapter(factory)
    files = FilesystemObjectBytesAdapter(root)
    return info, factory, uow, files


def test_duplicate_publish_reopen_and_read(tmp_path):
    root = tmp_path / "workspace"
    _, _, uow, files = components(root)
    publish = PublishObject(files, uow)
    first = publish(b"hello", "raw", "text/plain", "retain")
    assert publish(b"hello", "raw", "text/plain", "retain") == first
    _, _, reopened, reopened_files = components(root)
    assert ReadObject(reopened_files, reopened)(first.object_id) == b"hello"
    with reopened.transaction(write=False) as registry:
        assert registry.all() == (first,)
    report = InspectStorage(reopened_files, reopened)()
    assert report.checked == 1
    assert report.problems == ()


def test_metadata_conflict_is_not_silently_overwritten(tmp_path):
    _, _, uow, files = components(tmp_path / "workspace")
    publish = PublishObject(files, uow)
    original = publish(b"hello", "raw", "text/plain", "retain")
    for media, retention in [("application/json", "retain"), ("text/plain", "other")]:
        with pytest.raises(StorageError, match="metadata_conflict"):
            publish(b"hello", "raw", media, retention)
    with uow.transaction(write=False) as registry:
        assert registry.all() == (original,)


def test_file_publish_precedes_registry_write_and_retry_adopts_complete_orphan(tmp_path):
    _, _, uow, files = components(tmp_path / "workspace")

    class FailingUow:
        @contextmanager
        def transaction(self, *, write=True):
            with uow.transaction(write=write) as registry:
                yield registry
                # Inject the write/commit failure, not the new read-only preflight.
                if write:
                    raise RuntimeError("simulated rollback")

    with pytest.raises(RuntimeError, match="simulated rollback"):
        PublishObject(files, FailingUow())(b"hello", "raw", "text/plain", "retain")
    with uow.transaction(write=False) as registry:
        assert registry.all() == ()
    report = InspectStorage(files, uow)()
    assert [p.code for p in report.problems] == ["unregistered"]
    recovered = PublishObject(files, uow)(b"hello", "raw", "text/plain", "retain")
    assert ReadObject(files, uow)(recovered.object_id) == b"hello"
    assert InspectStorage(files, uow)().problems == ()


def test_real_process_exit_after_file_publication_then_replay(tmp_path):
    root = tmp_path / "workspace"
    _, _, uow, files = components(root)
    code = """import os, sys
from pathlib import Path
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
files = FilesystemObjectBytesAdapter(Path(sys.argv[1]))
files.publish(b'crash evidence', 'raw', 'text/plain', 'retain')
os._exit(73)
"""
    result = subprocess.run([sys.executable, "-c", code, str(root)], cwd=ROOT / "src", timeout=15)
    assert result.returncode == 73
    assert [p.code for p in InspectStorage(files, uow)().problems] == ["unregistered"]
    ref = PublishObject(files, uow)(b"crash evidence", "raw", "text/plain", "retain")
    read_code = """import sys
from pathlib import Path
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.application.queries.read_object import ReadObject
root = Path(sys.argv[1])
files = FilesystemObjectBytesAdapter(root)
uow = SqliteObjectUnitOfWorkAdapter(SqliteConnectionFactory(root))
sys.stdout.buffer.write(ReadObject(files, uow)(sys.argv[2]))
"""
    result = subprocess.run(
        [sys.executable, "-c", read_code, str(root), ref.object_id],
        cwd=ROOT / "src",
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"crash evidence"


@pytest.mark.parametrize("mode,code", [("missing", "missing"), ("corrupt", "corrupt")])
def test_missing_and_corrupt_bytes_are_not_reported_as_success(tmp_path, mode, code):
    root = tmp_path / "workspace"
    _, _, uow, files = components(root)
    ref = PublishObject(files, uow)(b"hello", "evidence", "text/plain", "retain")
    path = root / ref.relative_path
    if mode == "missing":
        path.unlink()
    else:
        path.write_bytes(b"other")
    with pytest.raises(StorageError, match=code):
        ReadObject(files, uow)(ref.object_id)
    report = InspectStorage(files, uow)()
    assert [(p.code, p.object_id) for p in report.problems] == [(code, ref.object_id)]
    with uow.transaction(write=False) as registry:
        assert registry.get(ref.object_id) == ref


def test_existing_corrupt_hash_path_is_never_overwritten(tmp_path):
    root = tmp_path / "workspace"
    _, _, uow, files = components(root)
    ref = PublishObject(files, uow)(b"hello", "raw", "text/plain", "retain")
    path = root / ref.relative_path
    path.write_bytes(b"other")
    with pytest.raises(StorageError, match="corrupt"):
        PublishObject(files, uow)(b"hello", "raw", "text/plain", "retain")
    assert path.read_bytes() == b"other"


def test_symlink_object_path_cannot_read_outside_workspace(tmp_path):
    root = tmp_path / "workspace"
    _, _, uow, files = components(root)
    ref = PublishObject(files, uow)(b"hello", "raw", "text/plain", "retain")
    path = root / ref.relative_path
    path.unlink()
    outside = tmp_path / "private"
    outside.write_bytes(b"hello")
    path.symlink_to(outside)
    with pytest.raises(StorageError, match="unsafe_path"):
        ReadObject(files, uow)(ref.object_id)
    assert any(p.code == "unsafe_path" for p in InspectStorage(files, uow)().problems)
    assert path.is_symlink()


def test_busy_write_fails_and_later_retry_succeeds(tmp_path):
    _, factory, uow, files = components(tmp_path / "workspace")
    held = factory.connect()
    held.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StorageError, match="database_busy"):
            PublishObject(files, uow)(b"hello", "raw", "text/plain", "retain")
    finally:
        held.rollback()
        held.close()
    ref = PublishObject(files, uow)(b"hello", "raw", "text/plain", "retain")
    assert ReadObject(files, uow)(ref.object_id) == b"hello"


def test_same_bytes_have_distinct_kind_identity(tmp_path):
    _, _, uow, files = components(tmp_path / "workspace")
    publish = PublishObject(files, uow)
    a = publish(b"", "raw", "application/octet-stream", "retain")
    b = publish(b"", "evidence", "application/octet-stream", "retain")
    assert a.object_id != b.object_id
    assert a.content_sha256 == hashlib.sha256(b"").hexdigest()
    assert ReadObject(files, uow)(a.object_id) == b""


def test_failed_file_sync_never_creates_registry_entry(tmp_path, monkeypatch):
    _, _, uow, files = components(tmp_path / "workspace")
    original = os.fsync

    def fail(fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(StorageError, match="file_io"):
        PublishObject(files, uow)(b"new", "raw", "text/plain", "retain")
    monkeypatch.setattr(os, "fsync", original)
    with uow.transaction(write=False) as registry:
        assert registry.all() == ()


def test_read_unknown_identity_is_explicit(tmp_path):
    _, _, uow, files = components(tmp_path / "workspace")
    with pytest.raises(StorageError, match="not_found"):
        ReadObject(files, uow)("missing")


def test_concurrent_replay_creates_one_registry_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    root = tmp_path / "workspace"
    components(root)

    def publish(_):
        files = FilesystemObjectBytesAdapter(root)
        uow = SqliteObjectUnitOfWorkAdapter(SqliteConnectionFactory(root, busy_timeout_ms=5000))
        return PublishObject(files, uow)(b"same", "raw", "text/plain", "retain")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(publish, range(8)))
    assert len(set(results)) == 1
    _, _, uow, files = components(root)
    assert InspectStorage(files, uow)().checked == 1


def test_read_transaction_rejects_writes(tmp_path):
    _, _, uow, files = components(tmp_path / "workspace")
    ref = files.publish(b"read-only", "raw", "text/plain", "retain")
    with pytest.raises(StorageError, match="database_error"):
        with uow.transaction(write=False) as registry:
            registry.add(ref)
    with uow.transaction(write=False) as registry:
        assert registry.all() == ()


def test_temporary_diagnostics_do_not_delete_interrupted_write(tmp_path):
    root = tmp_path / "workspace"
    _, _, uow, files = components(root)
    staging = root / "tmp/.object-incomplete"
    staging.write_bytes(b"part")
    report = InspectStorage(files, uow)()
    assert any(p.code == "temporary" and p.relative_path == "tmp/.object-incomplete" for p in report.problems)
    assert staging.read_bytes() == b"part"


def test_exception_preserves_rollback_and_closes_connection(tmp_path):
    _, _, uow, files = components(tmp_path / "workspace")
    ref = files.publish(b"new", "raw", "text/plain", "retain")
    with pytest.raises(StorageError, match="owner_failure"):
        with uow.transaction() as registry:
            registry.add(ref)
            raise StorageError("owner_failure")
    with uow.transaction(write=False) as registry:
        assert registry.all() == ()
    with uow.transaction() as registry:
        registry.add(ref)


def test_forged_registry_path_is_rejected_without_reading(tmp_path):
    root = tmp_path / "workspace"
    _, factory, uow, files = components(root)
    ref = PublishObject(files, uow)(b"bytes", "raw", "text/plain", "retain")
    connection = factory.connect()
    connection.execute(
        "UPDATE object_registry SET relative_path=? WHERE object_id=?", ("objects/other", ref.object_id)
    )
    connection.close()
    with pytest.raises(StorageError, match="invalid_object"):
        ReadObject(files, uow)(ref.object_id)
