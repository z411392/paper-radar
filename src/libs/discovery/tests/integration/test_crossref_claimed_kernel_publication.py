"""Real runtime-v15 claims, inbox and kernel files/registry, without provider HTTP.

Process-exit fault injection is not a physical power-loss test. Fixture SQL seeds
only the window/pass; reserve, dispatch, staging and publication use production code.
"""

import os
import socket
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import (
    KernelCrossrefCaptureStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_capture_claim_store_adapter import (
    SqliteCrossrefCaptureClaimStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_capture_inbox_adapter import (
    SqliteCrossrefCaptureInboxAdapter,
)
from libs.discovery.application.commands.publish_claimed_crossref_capture import PublishClaimedCrossrefCapture
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.exceptions.crossref_capture_claim_error import CrossrefCaptureClaimError
from libs.discovery.exceptions.crossref_capture_inbox_error import CrossrefCaptureInboxError
from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject

NOW = datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)
BODY = b'{"status":"ok","message-type":"work-list","message":{"items":[],"total-results":0}}'
EXIT = 47


def _forbid_network(*args, **kwargs):
    raise AssertionError("Publication/recovery must not open any network socket")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", _forbid_network)
    monkeypatch.setattr(socket, "create_connection", _forbid_network)


class Runtime:
    def __init__(self, root: Path, *, initialize: bool = False, scope: str = "statistics"):
        self.root = root
        bundle = load_workspace_migrations(with_runtime=True)
        if initialize:
            info = SqliteWorkspaceBootstrapAdapter(root, bundle).initialize()
            assert info.schema_version == 17 and not info.external_effects_enabled
        self.raw = SqliteConnectionFactory(root)
        self.schema = SqliteSchemaConnectionFactory(root, bundle, minimum_version=15)
        self.files = FilesystemObjectBytesAdapter(root)
        self.uow = SqliteObjectUnitOfWorkAdapter(self.raw)
        self.publish = PublishObject(self.files, self.uow)
        self.read = ReadObject(self.files, self.uow)
        self.claims = SqliteCrossrefCaptureClaimStoreAdapter(self.schema.connect)
        self.inbox = SqliteCrossrefCaptureInboxAdapter(self.schema.connect)
        self.captures = KernelCrossrefCaptureStoreAdapter(self.publish, self.read)
        self.source = CrossrefSourceAdapter()
        self.plan = self.source.compile(CrossrefWindowInput(
            "binding:publication", scope, NOW - timedelta(days=2), NOW - timedelta(days=1),
            "fixture@example.invalid", "publication-v1", 2,
        ))
        self.request = self.source.page(self.plan)
        self.pass_id = "pass:" + self.plan.query_fingerprint
        if initialize:
            self.add_window()

    def sql(self, sql, values=()):
        connection = self.schema.connect()
        try:
            return connection.execute(sql, values).fetchall()
        finally:
            connection.close()

    def add_window(self):
        window_id = "window:" + self.plan.query_fingerprint
        self.sql("INSERT INTO crossref_harvest_windows VALUES(?,?,?,?,?,?,?,'running',?,?)", (
            window_id, self.plan.definition.binding_key, self.plan.query_fingerprint,
            self.plan.definition.config_version, self.plan.definition.from_index.isoformat(),
            self.plan.definition.until_index.isoformat(), 2, NOW.isoformat(), NOW.isoformat(),
        ))
        self.sql("INSERT INTO crossref_harvest_passes "
                 "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
                 "VALUES(?,?,1,?,'running','*',?)", (
                     self.pass_id, window_id, self.plan.parameters_fingerprint, NOW.isoformat(),
                 ))

    def reserve(self, *, owner="worker:publication", now=NOW):
        workspace = self.sql("SELECT workspace_id,epoch FROM workspace_metadata")[0]
        return self.claims.reserve(
            self.plan, self.pass_id, self.request, owner_id=owner,
            expected_workspace_id=workspace[0], expected_epoch=workspace[1], now=now, lease_seconds=60,
        )

    def dispatch(self):
        # This is an isolated temporary workspace with socket access forbidden.
        self.sql("UPDATE workspace_metadata SET external_effects_enabled=1")
        claim = self.reserve()
        self.claims.begin_dispatch(claim, now=NOW + timedelta(seconds=1))
        return self.claims.read(claim.claim_id)

    def stage(self, claim, **changes):
        capture = replace(CrossrefHttpCapture(
            200, (("content-type", "application/json"),), BODY,
            NOW + timedelta(seconds=2), True, None,
        ), **changes)
        return self.inbox.stage(claim, capture, staged_at=NOW + timedelta(seconds=3))

    def publish_claim(self, claim):
        return PublishClaimedCrossrefCapture(self.inbox, self.captures)(claim)

    def object_rows(self):
        return tuple(tuple(row) for row in self.sql("SELECT * FROM object_registry ORDER BY object_id"))

    def assert_unresolved(self, claim):
        assert self.claims.read(claim.claim_id).state == "dispatching"
        assert self.sql("SELECT * FROM crossref_harvest_page_attempts") == []
        assert self.sql("SELECT current_cursor,next_page_no FROM crossref_harvest_passes "
                        "WHERE id=?", (claim.pass_id,))[0][:] == ("*", 0)
        with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_dispatch_already_started"):
            self.claims.begin_dispatch(claim, now=NOW + timedelta(days=1))
        with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_outcome_unknown"):
            self.reserve(owner="worker:replacement", now=NOW + timedelta(days=1))


def _child(root: str, claim_id: str, fault: str):
    socket.socket = _forbid_network
    socket.create_connection = _forbid_network
    runtime = Runtime(Path(root))
    claim = runtime.claims.read(claim_id)
    if fault == "after_dispatch":
        os._exit(EXIT)
    if fault == "inbox_before_commit":
        original = runtime.inbox._connect

        def connect():
            connection = original()

            def trace(statement):
                if statement == "COMMIT":
                    os._exit(EXIT)

            connection.set_trace_callback(trace)
            return connection

        runtime.inbox = SqliteCrossrefCaptureInboxAdapter(connect)
    runtime.stage(claim)
    if fault == "after_inbox":
        os._exit(EXIT)

    class CrashFiles(FilesystemObjectBytesAdapter):
        calls = 0

        def publish(self, content, kind, media_type, retention_policy):
            self.calls += 1
            label = "raw" if self.calls == 1 else "receipt"
            if fault == label + "_before_file":
                os._exit(EXIT)
            ref = super().publish(content, kind, media_type, retention_policy)
            if fault == label + "_after_file":
                os._exit(EXIT)
            return ref

    kernel = PublishObject(CrashFiles(runtime.root), runtime.uow)
    calls = 0

    def publish(*args):
        nonlocal calls
        calls += 1
        ref = kernel(*args)
        label = "raw" if calls == 1 else "receipt"
        if fault == label + "_after_registry":
            os._exit(EXIT)
        return ref

    captures = KernelCrossrefCaptureStoreAdapter(publish, runtime.read)
    PublishClaimedCrossrefCapture(runtime.inbox, captures)(claim)
    raise AssertionError("fault was not exercised")


@pytest.mark.parametrize("fault,inbox_exists,registered", [
    ("after_dispatch", False, 0), ("inbox_before_commit", False, 0),
    ("after_inbox", True, 0), ("raw_before_file", True, 0),
    ("raw_after_file", True, 0), ("raw_after_registry", True, 1),
    ("receipt_before_file", True, 1), ("receipt_after_file", True, 1),
    ("receipt_after_registry", True, 2),
])
def test_process_exit_boundaries_reopen_without_redispatch(tmp_path, fault, inbox_exists, registered):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    claim = runtime.dispatch()
    module = "libs.discovery.tests.integration.test_crossref_claimed_kernel_publication"
    script = f"from {module} import _child; _child({str(runtime.root)!r}, {claim.claim_id!r}, {fault!r})"
    child = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[4],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert child.returncode == EXIT, child.stdout + child.stderr
    reopened = Runtime(runtime.root)
    restored_claim = reopened.claims.read(claim.claim_id)
    assert len(reopened.object_rows()) == registered
    assert (reopened.inbox.load(restored_claim) is not None) == inbox_exists
    if inbox_exists:
        result = reopened.publish_claim(restored_claim)
        assert result.capture.body == BODY
        assert reopened.read(result.body_object_id) == BODY
        assert result.attempt_key == claim.claim_id
        before = reopened.object_rows()
        assert len(before) == 2
        assert reopened.publish_claim(restored_claim) == result
        assert reopened.object_rows() == before
    else:
        with pytest.raises(CrossrefCaptureInboxError, match="crossref_inbox_response_missing"):
            reopened.publish_claim(restored_claim)
    reopened.assert_unresolved(restored_claim)
    assert reopened.sql("PRAGMA integrity_check")[0][0] == "ok"
    assert reopened.sql("PRAGMA foreign_key_check") == []


@pytest.mark.parametrize("status,headers,complete,error", [
    (200, (), True, None), (403, (), True, None),
    (429, (("retry-after", "3600"),), True, None), (503, (), True, None),
    (302, (("location", "https://publisher.invalid/file"),), True, None),
    (200, (), False, "source_timeout"),
])
def test_response_evidence_is_not_page_success_or_new_dispatch_authority(
    tmp_path, status, headers, complete, error,
):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    claim = runtime.dispatch()
    record = runtime.stage(claim, status=status, headers=headers, complete=complete, capture_error=error)
    stored = runtime.publish_claim(claim)
    assert stored.capture == record.capture
    runtime.assert_unresolved(claim)


@pytest.mark.parametrize("target", ["raw", "receipt"])
@pytest.mark.parametrize("state", ["available", "missing", "quarantined"])
def test_replay_does_not_recreate_a_missing_registered_object(tmp_path, target, state):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    claim = runtime.dispatch()
    runtime.stage(claim)
    stored = runtime.publish_claim(claim)
    object_id = stored.body_object_id if target == "raw" else stored.receipt_id
    relative = runtime.sql("SELECT relative_path FROM object_registry WHERE object_id=?", (object_id,))[0][0]
    path = runtime.root / relative
    path.unlink()  # Fault injected only into this test's disposable workspace.
    runtime.sql("UPDATE object_registry SET state=? WHERE object_id=?", (state, object_id))
    before = runtime.object_rows()
    with pytest.raises(CrossrefCaptureInboxError, match="crossref_inbox_publication_failed"):
        runtime.publish_claim(claim)
    assert not path.exists(), "ordinary replay must not silently repair registered evidence"
    assert runtime.object_rows() == before
    assert runtime.inbox.load(claim).capture.body == BODY


@pytest.mark.parametrize("target", ["raw", "receipt"])
def test_corrupt_registered_bytes_are_not_overwritten(tmp_path, target):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    claim = runtime.dispatch()
    runtime.stage(claim)
    stored = runtime.publish_claim(claim)
    object_id = stored.body_object_id if target == "raw" else stored.receipt_id
    relative = runtime.sql("SELECT relative_path FROM object_registry WHERE object_id=?", (object_id,))[0][0]
    path = runtime.root / relative
    path.write_bytes(b"corruption fixture")
    with pytest.raises(CrossrefCaptureInboxError, match="crossref_inbox_publication_failed"):
        runtime.publish_claim(claim)
    assert path.read_bytes() == b"corruption fixture"


def test_identical_response_body_has_one_raw_object_but_distinct_claim_receipts(tmp_path):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    first = runtime.dispatch()
    runtime.stage(first)
    left = runtime.publish_claim(first)
    other = Runtime(runtime.root, scope="machine learning")
    other.add_window()
    second = other.dispatch()
    other.stage(second)
    right = other.publish_claim(second)
    assert left.body_object_id == right.body_object_id
    assert left.receipt_id != right.receipt_id
    assert left.attempt_key != right.attempt_key
    assert len(other.object_rows()) == 3


def test_concurrent_publishers_reuse_the_same_immutable_objects(tmp_path):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    claim = runtime.dispatch()
    runtime.stage(claim)
    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker():
        try:
            instance = Runtime(runtime.root)
            barrier.wait(timeout=5)
            results.append(instance.publish_claim(claim))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    assert len(results) == 2 and results[0] == results[1]
    assert len(runtime.object_rows()) == 2
    runtime.assert_unresolved(claim)


def test_filesystem_operations_do_not_hold_a_sqlite_writer_transaction(tmp_path):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    claim = runtime.dispatch()
    runtime.stage(claim)
    calls = []

    class ProbedFiles(FilesystemObjectBytesAdapter):
        def publish(self, *args):
            probe = SqliteConnectionFactory(runtime.root, busy_timeout_ms=0).connect()
            try:
                probe.execute("BEGIN IMMEDIATE")
                probe.execute("ROLLBACK")
                calls.append(True)
            finally:
                probe.close()
            return super().publish(*args)

    runtime.captures = KernelCrossrefCaptureStoreAdapter(
        PublishObject(ProbedFiles(runtime.root), runtime.uow), runtime.read,
    )
    runtime.publish_claim(claim)
    assert calls == [True, True]


@pytest.mark.parametrize("when", ["before", "after_raw", "after_receipt"])
def test_restore_epoch_prevents_current_looking_publication_result(tmp_path, when):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    claim = runtime.dispatch()
    runtime.stage(claim)
    if when == "before":
        runtime.sql("UPDATE workspace_metadata SET epoch=epoch+1")
    calls = []

    def publish(*args):
        result = runtime.publish(*args)
        calls.append(result.object_id)
        if (when == "after_raw" and len(calls) == 1) or (when == "after_receipt" and len(calls) == 2):
            runtime.sql("UPDATE workspace_metadata SET epoch=epoch+1")
        return result

    runtime.captures = KernelCrossrefCaptureStoreAdapter(publish, runtime.read)
    with pytest.raises(CrossrefCaptureInboxError, match="crossref_inbox_workspace_changed"):
        runtime.publish_claim(claim)
    if when == "before":
        assert calls == []
    assert runtime.sql("SELECT COUNT(*) FROM crossref_capture_inbox")[0][0] == 1
    assert runtime.claims.read(claim.claim_id).state == "dispatching"


def test_master_gate_disable_allows_evidence_publication_but_not_redispatch(tmp_path):
    runtime = Runtime(tmp_path / "workspace", initialize=True)
    claim = runtime.dispatch()
    runtime.stage(claim)
    runtime.sql("UPDATE workspace_metadata SET external_effects_enabled=0")
    assert runtime.publish_claim(claim).capture.body == BODY
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_dispatch_already_started"):
        runtime.claims.begin_dispatch(claim, now=NOW + timedelta(seconds=4))
    assert runtime.sql("SELECT external_effects_enabled FROM workspace_metadata")[0][0] == 0
