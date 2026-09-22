import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import ArxivAtomParserAdapter
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.application.commands.start_harvest_attempt import StartHarvestAttempt
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.discovery.application.queries.parse_source_page import ParseSourcePage
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
from libs.kernel.application.queries.read_object import ReadObject
from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError

ROOT = Path(__file__).resolve().parents[5]
NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)
AT = NOW + timedelta(seconds=5)
VERSION = "arxiv-atom-v1"


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "workspace"
    paths = sorted((ROOT / "migrations").glob("*.sql"))[:4]
    migrations = tuple(Migration(i, path.name, path.read_text()) for i, path in enumerate(paths, 1))
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    factory = SqliteConnectionFactory(root)
    connection = factory.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        with connection:
            connection.execute("INSERT INTO watch_profiles VALUES('personal','fixture','Fixture','active',NULL,?)", (NOW.isoformat(),))
            connection.execute("INSERT INTO watch_profile_revisions VALUES('personal',1,'fixture','{}',?,?)", ("a" * 64, NOW.isoformat()))
    finally:
        connection.close()
    compiler = ArxivQueryCompilerAdapter()
    query = SourceQueryInput("arxiv", "personal", 1, "a" * 64, DomainQuerySnapshot("statistics", 1, ("arxiv",), ("stat.ML",)), NOW, NOW + timedelta(days=1), ("arxiv",), page_size=2)
    journal = SqliteHarvestStoreAdapter(factory.connect, compiler)
    files = FilesystemObjectBytesAdapter(root)
    uow = SqliteObjectUnitOfWorkAdapter(factory)
    return SimpleNamespace(root=root, factory=factory, compiler=compiler, query=query, plan=compiler.compile(query), journal=journal, publish=PublishObject(files, uow), objects=ReadObject(files, uow), store=SqliteHarvestProcessingAdapter(factory.connect), parser=ParseSourcePage(ArxivAtomParserAdapter()))


def feed(start=0, ids=("2609.00001v1", "2609.00002v1"), total=3):
    entries = "".join(
        f"<entry><id>https://arxiv.org/abs/{identity}</id><title>Synthetic fixture</title>"
        "<summary>Not a real paper.</summary><author><name>Fixture</name></author>"
        "<category term='stat.ML'/><published>2026-09-22T00:00:00Z</published>"
        "<updated>2026-09-22T00:00:00Z</updated></entry>" for identity in ids
    )
    return (
        "<feed xmlns='http://www.w3.org/2005/Atom' xmlns:s='http://a9.com/-/spec/opensearch/1.1/'>"
        f"<s:totalResults>{total}</s:totalResults><s:startIndex>{start}</s:startIndex>"
        f"<s:itemsPerPage>{len(ids)}</s:itemsPerPage>{entries}</feed>"
    ).encode()


def saved(env, identity="page-a", start=0, ids=("2609.00001v1", "2609.00002v1"), total=3, *, body=None, status=200, error=None, capture_error=None, plan=None):
    plan = plan or env.plan
    request = env.compiler.page(plan, start)
    attempt = StartHarvestAttempt(env.journal)(plan, request, identity, NOW)
    raw = feed(start, ids, total) if body is None else body
    response = SourceHttpResponse(status, raw, (("content-type", "application/atom+xml"),), NOW + timedelta(seconds=1), capture_error)
    result = SourceFetchResult(request.request_fingerprint, response, hashlib.sha256(raw).hexdigest(), error, error is not None)
    RecordHarvestCapture(env.journal, env.publish)(attempt.attempt_id, result, NOW + timedelta(seconds=2))
    return ReadHarvestAttempt(env.journal)(identity), result


def process(env, *, parser=None, store=None, version=VERSION, objects=None):
    return ProcessHarvestPage(ReadHarvestAttempt(env.journal), objects or env.objects, parser or env.parser, store or env.store, version)


def database(env, sql, args=()):
    connection = env.factory.connect()
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def state(env, unit_id=None):
    query = "SELECT state,checkpoint_version,cursor_json,coverage_json FROM harvest_units"
    return database(env, query + (" WHERE id=?" if unit_id else ""), (unit_id,) if unit_id else ())[0]


def metadata(env, identity="page-a"):
    return json.loads(database(env, "SELECT response_metadata_json FROM harvest_attempts WHERE id=?", (identity,))[0][0])


def test_two_pages_commit_observations_and_checkpoint_together(env):
    attempt, _ = saved(env)
    first = process(env)("page-a", 0, AT)
    assert first.state == "partial" and first.checkpoint_version == 1 and first.next_start == 2
    assert len(first.observation_ids) == 2
    assert state(env)[:2] == ("partial", 1)
    saved(env, "page-b", 2, ("2609.00003v1",))
    final = process(env)("page-b", 1, AT)
    assert final.state == "succeeded" and final.next_start == 3 and final.checkpoint_version == 2
    rows = database(env, "SELECT native_id,payload_object_id,parser_version,observed_at,native_updated_at FROM source_observations ORDER BY native_id")
    assert len(rows) == 3 and rows[0][0] == "2609.00001v1"
    assert rows[0][1].startswith("raw:") and rows[0][2] == VERSION
    assert rows[0][3] == "2026-09-22T00:00:01.000000+00:00"
    assert rows[0][4] == "2026-09-22T00:00:00.000000+00:00"
    assert ReadHarvestAttempt(env.journal)("page-a") == attempt


def test_valid_empty_feed_is_not_empty_bytes(env):
    saved(env, ids=(), total=0)
    result = process(env)("page-a", 0, AT)
    assert result.state == "verified_empty" and result.checkpoint_version == 1
    assert result.observation_ids == () and result.total_results == 0
    assert database(env, "SELECT * FROM source_observations") == []
    assert len(database(env, "SELECT * FROM object_registry")) == 1


@pytest.mark.parametrize("body", [b"", b"<bad", b"<feed/>"])
def test_malformed_capture_does_not_advance_or_create_papers(env, body):
    saved(env, body=body)
    result = process(env)("page-a", 0, AT)
    assert result.state == "failed" and result.error_code and result.checkpoint_version == 0
    assert state(env)[1:] == (0, None, "{}")
    assert database(env, "SELECT * FROM source_observations") == []


@pytest.mark.parametrize("status,error,partial", [(429, "http_rate_limited", None), (503, "http_server_error", None), (200, "body_too_large", "body_too_large")])
def test_transport_failures_never_reach_parser(env, status, error, partial):
    saved(env, status=status, error=error, capture_error=partial)
    parser = Mock(side_effect=AssertionError("must not parse"))
    result = process(env, parser=parser)("page-a", 0, AT)
    assert result.error_code == error and result.state == "failed"
    parser.assert_not_called()
    assert state(env)[1] == 0


def test_timeout_without_response_keeps_failure_without_fake_body(env):
    request = env.compiler.page(env.plan)
    StartHarvestAttempt(env.journal)(env.plan, request, "timeout", NOW)
    RecordHarvestCapture(env.journal, env.publish)("timeout", SourceFetchResult(request.request_fingerprint, None, None, "http_timeout", True), AT)
    objects, parser = Mock(), Mock()
    result = process(env, parser=parser, objects=objects)("timeout", 0, AT)
    assert result.error_code == "http_timeout" and result.state == "failed"
    objects.assert_not_called()
    parser.assert_not_called()


def test_processing_requires_recorded_capture(env):
    StartHarvestAttempt(env.journal)(env.plan, env.compiler.page(env.plan), "open", NOW)
    with pytest.raises(HarvestError, match="capture_not_ready"):
        process(env)("open", 0, AT)
    assert state(env)[1] == 0


@pytest.mark.parametrize("corruption", ["missing", "changed"])
def test_lost_raw_does_not_cache_a_processing_failure(env, corruption):
    attempt, _ = saved(env)
    identity = json.loads(attempt.capture_json)["raw_object_id"].split(":")[1]
    path = env.root / f"objects/raw/{identity[:2]}/{identity}"
    if corruption == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupted fixture")
    with pytest.raises(StorageError):
        process(env)("page-a", 0, AT)
    assert "processing" not in metadata(env)
    assert state(env)[1] == 0


def test_replay_returns_original_receipt_after_later_page_not_current_status(env):
    attempt, result = saved(env)
    first = process(env)("page-a", 0, AT)
    saved(env, "page-b", 2, ("2609.00003v1",))
    process(env)("page-b", 1, AT)
    objects, parser = Mock(), Mock()
    repeated = process(env, objects=objects, parser=parser)("page-a", 0, AT + timedelta(days=1))
    assert repeated == first and repeated.state == "partial" and state(env)[0] == "succeeded"
    objects.assert_not_called()
    parser.assert_not_called()
    assert RecordHarvestCapture(env.journal, env.publish)("page-a", result, AT) == attempt
    assert "processing" in metadata(env)


def test_repaired_parser_version_may_retry_failed_raw(env):
    saved(env)
    def broken(request, body, *, http_status):
        from libs.discovery.exceptions.source_parse_error import SourceParseError
        raise SourceParseError("fixture_parser_failure")
    assert process(env, parser=broken)("page-a", 0, AT).state == "failed"
    def repaired(request, body, *, http_status):
        return replace(env.parser(request, body, http_status=http_status), parser_version="arxiv-atom-v2")
    result = process(env, parser=repaired, version="arxiv-atom-v2")("page-a", 0, AT)
    assert result.state == "partial" and len(metadata(env)["processing"]) == 2


def test_new_parser_cannot_reconsume_successful_page(env):
    saved(env)
    process(env)("page-a", 0, AT)
    with pytest.raises(HarvestError, match="checkpoint_offset_conflict"):
        process(env, version="arxiv-atom-v2")("page-a", 1, AT)
    assert len(database(env, "SELECT * FROM source_observations")) == 2


@pytest.mark.parametrize("version", [-1, True, 2**63, "0"])
def test_invalid_checkpoint_versions_rejected_before_reads(env, version):
    attempts = Mock()
    command = ProcessHarvestPage(attempts, Mock(), Mock(), Mock(), VERSION)
    with pytest.raises(HarvestError, match="invalid_checkpoint_version"):
        command("page-a", version, AT)
    attempts.assert_not_called()


def test_stale_checkpoint_rejected_before_parser(env):
    saved(env)
    parser = Mock()
    with pytest.raises(HarvestError, match="checkpoint_conflict"):
        process(env, parser=parser)("page-a", 1, AT)
    parser.assert_not_called()


def test_wrong_offset_cannot_skip_first_page(env):
    saved(env, start=2, ids=("2609.00003v1",))
    with pytest.raises(HarvestError, match="checkpoint_offset_conflict"):
        process(env)("page-a", 0, AT)
    assert state(env)[1] == 0


@pytest.mark.parametrize("ids,total,error", [(("2609.00002v1",), 3, "duplicate_traversal_identity"), (("2609.00003v1", "2609.00004v1"), 4, "source_result_set_changed"), ((), 3, "unexpected_empty_page")])
def test_traversal_drift_keeps_previous_progress(env, ids, total, error):
    saved(env)
    process(env)("page-a", 0, AT)
    saved(env, "page-b", 2, ids, total)
    before = state(env)
    result = process(env)("page-b", 1, AT)
    assert result.state == "partial" and result.error_code == error
    assert result.checkpoint_version == 1 and state(env) == before
    assert len(database(env, "SELECT * FROM source_observations")) == 2


def test_last_write_failure_rolls_back_observations_checkpoint_and_receipt(env):
    saved(env)
    database(env, "CREATE TRIGGER fail_processing BEFORE UPDATE ON harvest_attempts BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(HarvestError, match="harvest_database_error"):
        process(env)("page-a", 0, AT)
    assert database(env, "SELECT * FROM source_observations") == []
    assert state(env)[1:] == (0, None, "{}") and "processing" not in metadata(env)
    database(env, "DROP TRIGGER fail_processing")
    assert process(env)("page-a", 0, AT).checkpoint_version == 1


def test_concurrent_same_command_commits_once(env):
    saved(env)
    barrier = Barrier(2)
    class Synchronized:
        def snapshot(self, attempt, parser_version):
            value = env.store.snapshot(attempt, parser_version)
            barrier.wait(timeout=10)
            return value
        def commit(self, *args):
            return env.store.commit(*args)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: process(env, store=Synchronized())("page-a", 0, AT), range(2)))
    assert results[0] == results[1]
    assert state(env)[1] == 1 and len(database(env, "SELECT * FROM source_observations")) == 2


def test_late_failure_cannot_overwrite_newer_success(env):
    bad, _ = saved(env, "bad", body=b"<bad")
    saved(env, "good")
    class Overtaken:
        def snapshot(self, attempt, parser_version):
            return env.store.snapshot(attempt, parser_version)
        def commit(self, *args):
            process(env)("good", 0, AT)
            return env.store.commit(*args)
    with pytest.raises(HarvestError, match="checkpoint_conflict"):
        process(env, store=Overtaken())(bad.attempt_id, 0, AT)
    assert state(env)[:2] == ("partial", 1)
    assert "processing" not in metadata(env, "bad")


def test_success_in_one_window_does_not_hide_another_failure(env):
    good, _ = saved(env, ids=(), total=0)
    other = env.compiler.compile(replace(env.query, window_end=NOW + timedelta(days=2)))
    bad, _ = saved(env, "other", body=b"bad", plan=other)
    process(env)("page-a", 0, AT)
    process(env)("other", 0, AT)
    assert state(env, good.unit_id)[:2] == ("verified_empty", 1)
    assert state(env, bad.unit_id)[:2] == ("failed", 0)


@pytest.mark.parametrize("change", [lambda p: replace(p, parser_version="unexpected"), lambda p: replace(p, request_fingerprint="f" * 64), lambda p: replace(p, raw_body=b"different"), lambda p: replace(p, records=())])
def test_invalid_parser_contract_is_not_committed(env, change):
    saved(env)
    def parser(request, body, *, http_status):
        return change(env.parser(request, body, http_status=http_status))
    with pytest.raises(HarvestError, match="parsed_capture_mismatch"):
        process(env, parser=parser)("page-a", 0, AT)
    assert state(env)[1] == 0 and "processing" not in metadata(env)


@pytest.mark.parametrize("field,value", [("cursor_json", '{"next_start":999}'), ("coverage_json", '{"complete":true}'), ("checkpoint_version", 2)])
def test_corrupt_progress_is_not_reset_to_zero(env, field, value):
    saved(env)
    database(env, f"UPDATE harvest_units SET {field}=?", (value,))
    with pytest.raises(HarvestError):
        process(env)("page-a", 0, AT)
    assert database(env, "SELECT * FROM source_observations") == []


def test_query_does_not_create_missing_schema(tmp_path):
    database_path = tmp_path / "empty.sqlite3"
    def connect():
        connection = sqlite3.connect(database_path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
    store = SqliteHarvestProcessingAdapter(connect)
    attempt = SimpleNamespace(attempt_id="missing")
    with pytest.raises(HarvestError, match="harvest_database_error"):
        store.snapshot(attempt, VERSION)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT * FROM sqlite_master WHERE type='table'").fetchall() == []


def test_new_process_replays_receipt_without_parser(env):
    saved(env)
    process(env)("page-a", 0, AT)
    code = '''
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
factory = SqliteConnectionFactory(Path(sys.argv[1]))
journal = SqliteHarvestStoreAdapter(factory.connect, ArxivQueryCompilerAdapter())
objects, parser = Mock(), Mock()
command = ProcessHarvestPage(ReadHarvestAttempt(journal), objects, parser,
    SqliteHarvestProcessingAdapter(factory.connect), "arxiv-atom-v1")
result = command("page-a", 0, datetime(2026, 9, 23, tzinfo=timezone.utc))
assert result.checkpoint_version == 1 and result.state == "partial"
objects.assert_not_called()
parser.assert_not_called()
print("DURABLE_RECEIPT_OK")
'''
    result = subprocess.run([sys.executable, "-I", "-c", code, str(env.root)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "DURABLE_RECEIPT_OK"
