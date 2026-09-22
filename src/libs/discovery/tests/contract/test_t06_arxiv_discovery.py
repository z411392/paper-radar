import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.application.commands.start_harvest_attempt import StartHarvestAttempt
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import SqliteWorkspaceBootstrapAdapter
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.dtos.migration import Migration

ROOT = Path(__file__).resolve().parents[5]
NOW = datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "workspace"
    migrations = tuple(
        Migration(index, path.name, path.read_text(encoding="utf-8"))
        for index, path in enumerate(sorted((ROOT / "migrations").glob("*.sql"))[:4], 1)
    )
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    factory = SqliteConnectionFactory(root)
    connection = factory.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        with connection:
            connection.execute(
                "INSERT INTO watch_profiles VALUES('personal','fixture','Fixture','active',NULL,?)",
                (NOW.isoformat(),),
            )
            connection.execute(
                "INSERT INTO watch_profile_revisions VALUES('personal',1,'fixture','{}',?,?)",
                ("a" * 64, NOW.isoformat()),
            )
    finally:
        connection.close()
    compiler = ArxivQueryCompilerAdapter()
    query = SourceQueryInput(
        "arxiv", "personal", 1, "a" * 64,
        DomainQuerySnapshot("statistics", 1, ("arxiv",), ("stat.ML",)),
        NOW, NOW + timedelta(days=1), ("arxiv",), page_size=2,
    )
    plan = compiler.compile(query)
    request = compiler.page(plan)
    store = SqliteHarvestStoreAdapter(factory.connect, compiler)
    publish = PublishObject(FilesystemObjectBytesAdapter(root), SqliteObjectUnitOfWorkAdapter(factory))
    return root, factory, compiler, query, plan, request, store, publish


def capture(request, body=b"synthetic raw; not parsed", status=200, failure=None, capture_error=None):
    response = SourceHttpResponse(
        status, body, (("content-type", "application/atom+xml"),), NOW + timedelta(seconds=1), capture_error,
    )
    return SourceFetchResult(
        request.request_fingerprint, response, hashlib.sha256(body).hexdigest(), failure,
        failure is not None, 3.0 if failure else None,
    )


def rows(factory, table):
    assert table in {"source_bindings", "harvest_units", "harvest_attempts", "source_observations", "object_registry"}
    connection = factory.connect()
    try:
        return connection.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        connection.close()


def started(env, identity="attempt-a"):
    return StartHarvestAttempt(env[6])(env[4], env[5], identity, NOW)


def test_start_is_idempotent_and_keeps_exact_query(env):
    first = started(env)
    again = StartHarvestAttempt(env[6])(env[4], env[5], "attempt-a", NOW + timedelta(seconds=3))
    assert first == again
    assert first.attempt_no == 1 and first.state == "running" and first.capture_json is None
    assert len(rows(env[1], "source_bindings")) == len(rows(env[1], "harvest_units")) == 1
    assert len(rows(env[1], "harvest_attempts")) == 1
    assert rows(env[1], "source_bindings")[0][5] == env[4].provenance_json


def test_capture_is_durable_idempotent_and_does_not_advance_checkpoint(env):
    attempt = started(env)
    result = capture(env[5])
    unit_before = rows(env[1], "harvest_units")
    saved = RecordHarvestCapture(env[6], env[7])(attempt.attempt_id, result, NOW + timedelta(seconds=2))
    repeated = RecordHarvestCapture(env[6], env[7])(attempt.attempt_id, result, NOW + timedelta(seconds=4))
    assert repeated == saved
    restored = ReadHarvestAttempt(SqliteHarvestStoreAdapter(env[1].connect, env[2]))(attempt.attempt_id)
    assert restored == saved and saved.state == "captured"
    metadata = json.loads(saved.capture_json)
    digest = hashlib.sha256(result.response.body).hexdigest()
    assert metadata["raw_object_id"] == "raw:" + digest
    assert metadata["body_complete"] is True
    assert (env[0] / f"objects/raw/{digest[:2]}/{digest}").read_bytes() == result.response.body
    assert rows(env[1], "harvest_units") == unit_before
    assert rows(env[1], "source_observations") == []
    assert len(rows(env[1], "object_registry")) == 1


@pytest.mark.parametrize("status,error", [(429, "http_rate_limited"), (503, "http_server_error"), (401, "http_auth")])
def test_failed_http_body_is_preserved_not_verified_empty(env, status, error):
    attempt = started(env)
    saved = RecordHarvestCapture(env[6], env[7])(
        attempt.attempt_id, capture(env[5], b"failure body", status, error), NOW + timedelta(seconds=2),
    )
    assert saved.state == "failed"
    meta = json.loads(saved.capture_json)
    assert meta["status"] == status and meta["failure_code"] == error and meta["raw_object_id"]
    assert rows(env[1], "harvest_units")[0][4] not in {"succeeded", "verified_empty"}


def test_timeout_without_response_is_not_fabricated_empty_body(env):
    attempt = started(env)
    result = SourceFetchResult(env[5].request_fingerprint, None, None, "http_timeout", True)
    saved = RecordHarvestCapture(env[6], env[7])(attempt.attempt_id, result, NOW + timedelta(seconds=2))
    assert saved.state == "failed" and json.loads(saved.capture_json)["raw_object_id"] is None
    assert rows(env[1], "object_registry") == []


def test_partial_response_retains_prefix_semantics(env):
    attempt = started(env)
    result = capture(env[5], b"partial", 503, "body_too_large", "body_too_large")
    saved = RecordHarvestCapture(env[6], env[7])(attempt.attempt_id, result, NOW + timedelta(seconds=2))
    meta = json.loads(saved.capture_json)
    assert meta["body_complete"] is False and meta["hash_scope"] == "captured_prefix"
    assert meta["retry_after_seconds"] == 3.0


@pytest.mark.parametrize("mutation", [
    lambda r: replace(r, request_fingerprint="f" * 64),
    lambda r: replace(r, response_sha256="f" * 64),
    lambda r: replace(r, response=None),
    lambda r: replace(r, retryable=1),
    lambda r: replace(r, retry_after_seconds=float("nan")),
    lambda r: replace(r, retry_after_seconds=-1.0),
    lambda r: replace(r, response=replace(r.response, status=True)),
    lambda r: replace(r, response=replace(r.response, status=500)),
    lambda r: replace(r, response=replace(r.response, capture_error="truncated")),
    lambda r: replace(r, response=replace(r.response, received_at=NOW.replace(tzinfo=None))),
    lambda r: replace(r, response=replace(r.response, headers=(("authorization", "private"),))),
    lambda r: replace(r, response=replace(r.response, body=b"x" * 8_000_001)),
])
def test_invalid_capture_fails_before_object_write(env, mutation):
    attempt = started(env)
    publisher = Mock(side_effect=AssertionError("must not write"))
    with pytest.raises(HarvestError):
        RecordHarvestCapture(env[6], publisher)(attempt.attempt_id, mutation(capture(env[5])), NOW + timedelta(seconds=2))
    publisher.assert_not_called()
    assert ReadHarvestAttempt(env[6])(attempt.attempt_id).state == "running"


def test_same_attempt_cannot_replace_recorded_response(env):
    attempt = started(env)
    use_case = RecordHarvestCapture(env[6], env[7])
    first = use_case(attempt.attempt_id, capture(env[5]), NOW + timedelta(seconds=2))
    with pytest.raises(HarvestError, match="capture_conflict"):
        use_case(attempt.attempt_id, capture(env[5], b"different"), NOW + timedelta(seconds=2))
    assert ReadHarvestAttempt(env[6])(attempt.attempt_id) == first
    assert len(rows(env[1], "object_registry")) == 1


def test_attempt_identity_cannot_be_reused_for_another_page(env):
    started(env)
    with pytest.raises(HarvestError, match="attempt_conflict"):
        StartHarvestAttempt(env[6])(env[4], env[2].page(env[4], 2), "attempt-a", NOW)
    assert len(rows(env[1], "harvest_attempts")) == 1


def test_missing_profile_reference_rolls_back_new_binding_and_unit(env):
    plan = env[2].compile(replace(env[3], profile_id="missing"))
    with pytest.raises(HarvestError):
        StartHarvestAttempt(env[6])(plan, env[2].page(plan), "attempt-x", NOW)
    assert rows(env[1], "source_bindings") == rows(env[1], "harvest_units") == []


@pytest.mark.parametrize("identity", ["", "../escape", "x" * 129, "a\n", None, 3])
def test_invalid_attempt_identity_rejected(env, identity):
    with pytest.raises(HarvestError):
        StartHarvestAttempt(env[6])(env[4], env[5], identity, NOW)
    assert rows(env[1], "source_bindings") == []


def test_unknown_attempt_fails_before_publication(env):
    publisher = Mock()
    with pytest.raises(HarvestError, match="attempt_missing"):
        RecordHarvestCapture(env[6], publisher)("missing", capture(env[5]), NOW + timedelta(seconds=2))
    publisher.assert_not_called()


def test_database_capture_failure_preserves_object_and_retry_adopts_it(env):
    attempt = started(env)
    conn = env[1].connect()
    try:
        conn.execute("CREATE TRIGGER fail_capture BEFORE UPDATE ON harvest_attempts BEGIN SELECT RAISE(ABORT,'fixture'); END")
    finally:
        conn.close()
    result = capture(env[5])
    with pytest.raises(HarvestError, match="harvest_database_error"):
        RecordHarvestCapture(env[6], env[7])(attempt.attempt_id, result, NOW + timedelta(seconds=2))
    assert ReadHarvestAttempt(env[6])(attempt.attempt_id).capture_json is None
    assert len(rows(env[1], "object_registry")) == 1
    conn = env[1].connect()
    try:
        conn.execute("DROP TRIGGER fail_capture")
    finally:
        conn.close()
    assert RecordHarvestCapture(env[6], env[7])(
        attempt.attempt_id, result, NOW + timedelta(seconds=2),
    ).state == "captured"
    assert len(rows(env[1], "object_registry")) == 1


def test_object_write_failure_keeps_attempt_open(env):
    attempt = started(env)
    publisher = Mock(side_effect=OSError("fixture"))
    with pytest.raises(OSError):
        RecordHarvestCapture(env[6], publisher)(attempt.attempt_id, capture(env[5]), NOW + timedelta(seconds=2))
    assert ReadHarvestAttempt(env[6])(attempt.attempt_id).capture_json is None


def test_concurrent_connections_allocate_unique_attempt_numbers(env):
    def run(index):
        store = SqliteHarvestStoreAdapter(env[1].connect, env[2])
        return StartHarvestAttempt(store)(env[4], env[5], f"attempt-{index}", NOW).attempt_no
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(run, range(8)))
    assert sorted(numbers) == list(range(1, 9))
    assert len(rows(env[1], "harvest_units")) == 1


def test_new_process_reads_capture_without_network_or_refetch(env):
    attempt = started(env)
    RecordHarvestCapture(env[6], env[7])(attempt.attempt_id, capture(env[5]), NOW + timedelta(seconds=2))
    code = '''
import json, sys
from pathlib import Path
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
store=SqliteHarvestStoreAdapter(SqliteConnectionFactory(Path(sys.argv[1])).connect,ArxivQueryCompilerAdapter())
row=ReadHarvestAttempt(store)("attempt-a")
assert row.state=="captured"
print(json.loads(row.capture_json)["raw_object_id"])
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(env[0])], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("raw:")


def test_empty_success_body_is_captured_but_not_verified_empty(env):
    attempt = started(env)
    saved = RecordHarvestCapture(env[6], env[7])(attempt.attempt_id, capture(env[5], b""), NOW + timedelta(seconds=2))
    assert saved.state == "captured"
    assert json.loads(saved.capture_json)["byte_size"] == 0
    assert rows(env[1], "harvest_units")[0][4] != "verified_empty"


def test_no_schema_is_created_implicitly(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    def connect():
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    store = SqliteHarvestStoreAdapter(connect, ArxivQueryCompilerAdapter())
    with pytest.raises(HarvestError, match="harvest_database_error"):
        ReadHarvestAttempt(store)("missing")
    conn = connect()
    try:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    finally:
        conn.close()


def test_missing_foreign_key_enforcement_is_rejected(env):
    def unsafe():
        return sqlite3.connect(env[0] / "state/app.sqlite3", isolation_level=None)
    with pytest.raises(HarvestError, match="foreign_keys_required"):
        ReadHarvestAttempt(SqliteHarvestStoreAdapter(unsafe, env[2]))("missing")


def test_real_process_crash_after_publish_does_not_forge_capture(env):
    started(env)
    body = b"synthetic raw; not parsed"
    code = '''
import os,sys
from pathlib import Path
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.application.commands.publish_object import PublishObject
root=Path(sys.argv[1])
PublishObject(FilesystemObjectBytesAdapter(root),SqliteObjectUnitOfWorkAdapter(SqliteConnectionFactory(root)))(b"synthetic raw; not parsed","raw","application/octet-stream","source-response")
os._exit(23)
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(env[0])], capture_output=True)
    assert result.returncode == 23
    assert ReadHarvestAttempt(env[6])("attempt-a").capture_json is None
    saved = RecordHarvestCapture(env[6], env[7])("attempt-a", capture(env[5], body), NOW + timedelta(seconds=2))
    assert saved.state == "captured" and len(rows(env[1], "object_registry")) == 1
