"""Ordinary publication may adopt orphans; it may not repair registered evidence."""

import hashlib
from contextlib import contextmanager

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.exceptions.storage_error import StorageError

BODY = b"kernel publication regression"


@pytest.fixture
def storage(tmp_path):
    root = tmp_path / "workspace"
    SqliteWorkspaceBootstrapAdapter(root, load_workspace_migrations()).initialize()
    factory = SqliteConnectionFactory(root)
    files = FilesystemObjectBytesAdapter(root)
    uow = SqliteObjectUnitOfWorkAdapter(factory)
    return root, factory, files, uow


def test_exact_registered_replay_reads_without_new_file_publication(storage):
    root, factory, files, uow = storage
    original = PublishObject(files, uow)(BODY, "raw", "text/plain", "test")
    before = (root / original.relative_path).stat()

    class ReadOnlyFiles(FilesystemObjectBytesAdapter):
        def publish(self, *args):
            raise AssertionError("a registered replay is read-only")

    assert PublishObject(ReadOnlyFiles(root), uow)(BODY, "raw", "text/plain", "test") == original
    after = (root / original.relative_path).stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


@pytest.mark.parametrize("media,retention", [("application/json", "test"), ("text/plain", "other")])
def test_metadata_conflict_is_checked_before_any_file_publication(storage, media, retention):
    root, factory, files, uow = storage
    original = PublishObject(files, uow)(BODY, "raw", "text/plain", "test")
    (root / original.relative_path).unlink()
    with pytest.raises(StorageError, match="metadata_conflict"):
        PublishObject(files, uow)(BODY, "raw", media, retention)
    assert not (root / original.relative_path).exists()


@pytest.mark.parametrize("content", [None, "text", bytearray(b"text"), 1])
def test_invalid_content_fails_without_creating_object_directories(storage, content):
    root, factory, files, uow = storage
    with pytest.raises(StorageError, match="invalid_object"):
        PublishObject(files, uow)(content, "raw", "text/plain", "test")
    assert list((root / "objects").iterdir()) == []


def test_complete_unregistered_orphan_is_adopted_without_replacing_inode(storage):
    root, factory, files, uow = storage
    orphan = files.publish(BODY, "raw", "text/plain", "test")
    path = root / orphan.relative_path
    before = path.stat().st_ino
    result = PublishObject(files, uow)(BODY, "raw", "text/plain", "test")
    assert result.object_id == orphan.object_id
    assert path.stat().st_ino == before
    with uow.transaction(write=False) as registry:
        assert registry.get(result.object_id) == result


def test_registry_rollback_leaves_recoverable_orphan(storage):
    root, factory, files, uow = storage

    class FailingUow:
        @contextmanager
        def transaction(self, *, write=True):
            with uow.transaction(write=write) as registry:
                yield registry
                if write:
                    raise RuntimeError("injected before registry commit")

    with pytest.raises(RuntimeError, match="injected before registry commit"):
        PublishObject(files, FailingUow())(BODY, "raw", "text/plain", "test")
    object_id = "raw:" + hashlib.sha256(BODY).hexdigest()
    with uow.transaction(write=False) as registry:
        assert registry.get(object_id) is None
    result = PublishObject(files, uow)(BODY, "raw", "text/plain", "test")
    assert result.object_id == object_id
    assert files.read(result) == BODY


def test_registry_change_during_read_cannot_return_stale_available_reference(storage):
    root, factory, files, uow = storage
    original = PublishObject(files, uow)(BODY, "raw", "text/plain", "test")

    class QuarantiningReader(FilesystemObjectBytesAdapter):
        def read(self, ref):
            result = super().read(ref)
            connection = factory.connect()
            try:
                connection.execute("UPDATE object_registry SET state='quarantined' WHERE object_id=?",
                                   (ref.object_id,))
            finally:
                connection.close()
            return result

    with pytest.raises(StorageError, match="object_changed"):
        PublishObject(QuarantiningReader(root), uow)(BODY, "raw", "text/plain", "test")
    assert (root / original.relative_path).read_bytes() == BODY


def test_registered_file_read_also_runs_outside_writer_transaction(storage):
    root, factory, files, uow = storage
    original = PublishObject(files, uow)(BODY, "raw", "text/plain", "test")
    calls = []

    class ProbeReader(FilesystemObjectBytesAdapter):
        def read(self, ref):
            connection = SqliteConnectionFactory(root, busy_timeout_ms=0).connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ROLLBACK")
                calls.append(True)
            finally:
                connection.close()
            return super().read(ref)

    assert PublishObject(ProbeReader(root), uow)(BODY, "raw", "text/plain", "test") == original
    assert calls == [True]
