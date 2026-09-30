import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from libs.discovery.adapters.driven.arxiv_atom_parser_adapter import ArxivAtomParserAdapter
from libs.discovery.adapters.driven.arxiv_query_compiler_adapter import ArxivQueryCompilerAdapter
from libs.discovery.adapters.driven.sqlite_harvest_processing_adapter import SqliteHarvestProcessingAdapter
from libs.discovery.adapters.driven.sqlite_harvest_resume_adapter import SqliteHarvestResumeAdapter
from libs.discovery.adapters.driven.sqlite_harvest_store_adapter import SqliteHarvestStoreAdapter
from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.application.commands.start_harvest_attempt import StartHarvestAttempt
from libs.discovery.application.queries.parse_source_page import ParseSourcePage
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
from libs.discovery.application.queries.read_harvest_resume import ReadHarvestResume
from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.tests.contract import test_harvest_page_processing as prior
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import SqliteObjectUnitOfWorkAdapter
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.kernel.exceptions.storage_error import StorageError
from libs.research_workflow.application.commands.run_harvest_slice import RunHarvestSlice
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.watch_profiles.adapters.driven.sqlite_watch_profile_store_adapter import (
    SqliteWatchProfileStoreAdapter,
)
from libs.watch_profiles.application.queries.read_watch_profile import ReadWatchProfile

NOW, AT, VERSION = prior.NOW, prior.AT, prior.VERSION


def filters():
    return {
        "sources": ["arxiv"],
        "include": [],
        "exclude": [],
        "languages": [],
        "free_only": False,
        "allow_preprints": True,
    }


def reopen(root):
    factory = SqliteConnectionFactory(root)
    compiler = ArxivQueryCompilerAdapter()
    query = SourceQueryInput(
        "arxiv",
        "personal",
        1,
        "a" * 64,
        DomainQuerySnapshot("statistics", 1, ("arxiv",), ("stat.ML",)),
        NOW,
        NOW + timedelta(days=1),
        ("arxiv",),
        page_size=2,
    )
    journal = SqliteHarvestStoreAdapter(factory.connect, compiler)
    files, uow = FilesystemObjectBytesAdapter(root), SqliteObjectUnitOfWorkAdapter(factory)
    return SimpleNamespace(
        root=root,
        factory=factory,
        compiler=compiler,
        query=query,
        plan=compiler.compile(query),
        journal=journal,
        publish=PublishObject(files, uow),
        objects=ReadObject(files, uow),
        store=SqliteHarvestProcessingAdapter(factory.connect),
        parser=ParseSourcePage(ArxivAtomParserAdapter()),
        profiles=ReadWatchProfile(SqliteWatchProfileStoreAdapter(factory.connect)),
    )


@pytest.fixture
def env(tmp_path):
    old = prior.env.__wrapped__(tmp_path)
    prior.database(old, "UPDATE watch_profiles SET published_revision=1")
    prior.database(
        old,
        "UPDATE watch_profile_revisions SET scope_text='',filters_json=?",
        (json.dumps(filters(), sort_keys=True, separators=(",", ":")),),
    )
    prior.database(
        old, "INSERT INTO domain_definitions VALUES('statistics','Fixture','{}',1,?)", (NOW.isoformat(),)
    )
    prior.database(old, "INSERT INTO watch_profile_domains VALUES('personal',1,'statistics',1)")
    return reopen(old.root)


class Runtime:
    def __init__(self):
        self.value = AT
        self.sleep_calls = []

    def now(self):
        return self.value

    def new_attempt_id(self):
        return "attempt:" + uuid4().hex

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        self.value += timedelta(seconds=seconds)


class Source:
    def __init__(self, runtime, *, error=None, retryable=True, delay=None, body=None, total=3):
        self.runtime, self.error, self.retryable, self.delay = runtime, error, retryable, delay
        self.body, self.total, self.calls = body, total, []
        self.after_fetch = None

    def __call__(self, request):
        self.calls.append(request)
        if self.after_fetch is not None:
            self.after_fetch()
        if self.error == "http_timeout":
            return SourceFetchResult(
                request.request_fingerprint, None, None, self.error, self.retryable, self.delay
            )
        ids = tuple(
            f"2609.{i + 1:05d}v1"
            for i in range(request.start, min(self.total, request.start + request.max_results))
        )
        raw = self.body if self.body is not None else prior.feed(request.start, ids, self.total)
        response = SourceHttpResponse(
            429 if self.error else 200, raw, (("content-type", "application/atom+xml"),), self.runtime.now()
        )
        return SourceFetchResult(
            request.request_fingerprint,
            response,
            hashlib.sha256(raw).hexdigest(),
            self.error,
            self.retryable if self.error else False,
            self.delay,
        )


def resume(env, plan=None):
    return ReadHarvestResume(SqliteHarvestResumeAdapter(env.factory.connect, env.compiler))(
        plan or env.plan, VERSION
    )


def workflow(env, source=None, runtime=None, *, profiles=None, record=None, processor=None):
    runtime = runtime or Runtime()
    source = source or Source(runtime)
    process = ProcessHarvestPage(ReadHarvestAttempt(env.journal), env.objects, env.parser, env.store, VERSION)
    runner = RunHarvestSlice(
        env.compiler,
        ReadHarvestResume(SqliteHarvestResumeAdapter(env.factory.connect, env.compiler)),
        StartHarvestAttempt(env.journal),
        source,
        record or RecordHarvestCapture(env.journal, env.publish),
        processor or process,
        profiles or env.profiles,
        runtime,
        VERSION,
    )
    return runner, source, runtime


def count(env, table):
    assert table in {"harvest_attempts", "source_observations", "object_registry", "harvest_units"}
    return prior.database(env, f"SELECT COUNT(*) FROM {table}")[0][0]


def test_unused_resume_is_read_only_and_not_a_created_unit(env):
    current = resume(env)
    assert current.state == "pending" and current.checkpoint_version == current.next_start == 0
    assert current.attempt is None and current.total_results is None
    assert count(env, "harvest_units") == count(env, "harvest_attempts") == 0


def test_full_two_page_flow_uses_durable_state_and_real_profile_reader(env):
    run, source, _ = workflow(env)
    result = run(env.query, max_pages=2)
    assert result.stop_reason == "complete" and result.progress.state == "succeeded"
    assert result.progress.next_start == 3 and result.progress.checkpoint_version == 2
    assert result.fetch_count == result.processed_pages == 2 and result.reused_captures == 0
    assert [r.start for r in source.calls] == [0, 2]
    assert count(env, "source_observations") == 3
    assert count(env, "harvest_attempts") == count(env, "object_registry") == 2


def test_budget_then_reopen_only_fetches_unfinished_page(env):
    first, source, _ = workflow(env)
    limited = first(env.query, max_pages=1)
    assert limited.stop_reason == "page_budget" and limited.progress.next_start == 2
    new = reopen(env.root)
    run, second, _ = workflow(new)
    done = run(new.query, max_pages=1)
    assert done.stop_reason == "complete" and [r.start for r in second.calls] == [2]
    assert [r.start for r in source.calls] == [0]
    assert count(env, "source_observations") == 3


def test_completed_replay_does_not_fetch_or_add_attempts(env):
    run, _, _ = workflow(env)
    run(env.query, max_pages=2)
    forbidden = Mock(side_effect=AssertionError("completed work cannot fetch"))
    again, _, _ = workflow(reopen(env.root), forbidden)
    result = again(env.query, max_pages=2)
    assert result.stop_reason == "complete" and result.fetch_count == result.processed_pages == 0
    forbidden.assert_not_called()
    assert count(env, "harvest_attempts") == 2


def test_saved_capture_is_used_without_fetch(env):
    prior.saved(env)
    forbidden = Mock(side_effect=AssertionError("durable capture must be reused"))
    run, _, _ = workflow(env, forbidden)
    result = run(env.query, max_pages=1)
    assert result.reused_captures == result.processed_pages == 1 and result.fetch_count == 0
    assert result.progress.next_start == 2
    forbidden.assert_not_called()


def test_saved_capture_has_priority_over_newer_uncaptured_attempt(env):
    prior.saved(env)
    StartHarvestAttempt(env.journal)(env.plan, env.compiler.page(env.plan), "later-running", AT)
    assert resume(env).attempt.attempt_id == "page-a"
    result = workflow(env)[0](env.query, max_pages=1)
    assert result.fetch_count == 0 and result.progress.next_start == 2


def test_running_attempt_is_preserved_and_new_get_has_new_identity(env):
    StartHarvestAttempt(env.journal)(env.plan, env.compiler.page(env.plan), "interrupted", NOW)
    run, source, _ = workflow(env)
    result = run(env.query, max_pages=1)
    assert result.fetch_count == 1 and len(source.calls) == 1
    old = ReadHarvestAttempt(env.journal)("interrupted")
    assert old.state == "running" and old.capture_json is None
    assert count(env, "harvest_attempts") == 2


def test_empty_valid_feed_finishes_without_synthetic_observations(env):
    clock = Runtime()
    run, _, _ = workflow(env, Source(clock, total=0), clock)
    result = run(env.query)
    assert result.stop_reason == "complete" and result.progress.state == "verified_empty"
    assert result.progress.total_results == 0 and count(env, "source_observations") == 0


@pytest.mark.parametrize("body", [b"", b"<not-xml", b"<html>gateway</html>"])
def test_parse_failure_is_preserved_and_not_refetched(env, body):
    clock = Runtime()
    source = Source(clock, body=body)
    run, _, _ = workflow(env, source, clock)
    result = run(env.query)
    assert result.stop_reason == "processing_failed" and result.progress.checkpoint_version == 0
    again = run(env.query, retry_failed=True)
    assert again.stop_reason == "processing_failed" and len(source.calls) == 1
    assert count(env, "source_observations") == 0


class LocalDeferThenSuccess:
    def __init__(self, runtime, *, delay=3.0):
        self.runtime = runtime
        self.delay = delay
        self.calls = []
        self.success = Source(runtime)

    def __call__(self, request):
        self.calls.append(request)
        if len(self.calls) == 1:
            return SourceFetchResult(
                request.request_fingerprint,
                None,
                None,
                "provider_deferred",
                True,
                self.delay,
            )
        return self.success(request)


def test_local_provider_defer_waits_without_durable_failed_capture(env):
    clock = Runtime()
    source = LocalDeferThenSuccess(clock)
    run, _, _ = workflow(env, source, clock)

    result = run(env.query, max_pages=2)

    assert result.stop_reason == "complete"
    assert clock.sleep_calls == [3.0]
    assert len(source.calls) == 3
    assert count(env, "harvest_attempts") == 2
    assert prior.database(
        env,
        "SELECT COUNT(*) FROM harvest_attempts WHERE state='failed'",
    )[0][0] == 0


def test_long_provider_defer_remains_durable_retry_not_local_sleep(env):
    clock = Runtime()
    source = Source(
        clock,
        error="provider_deferred",
        delay=30.0,
    )
    run, _, _ = workflow(env, source, clock)

    result = run(env.query)

    assert result.stop_reason == "source_failed"
    assert clock.sleep_calls == []
    assert len(source.calls) == 1
    assert count(env, "harvest_attempts") == 1


@pytest.mark.parametrize("error", ["http_timeout", "http_rate_limited"])
def test_source_failure_requires_explicit_retry_and_cooldown(env, error):
    clock = Runtime()
    source = Source(clock, error=error, delay=30.0)
    run, _, _ = workflow(env, source, clock)
    assert run(env.query).stop_reason == "source_failed"
    assert run(env.query).stop_reason == "retry_required"
    assert run(env.query, retry_failed=True).stop_reason == "cooldown"
    assert len(source.calls) == 1
    clock.value += timedelta(seconds=30)
    source.error, source.delay = None, None
    done = run(env.query, max_pages=2, retry_failed=True)
    assert done.stop_reason == "complete" and len(source.calls) == 3
    assert count(env, "harvest_attempts") == 3


def test_nonretryable_source_error_is_never_automatically_retried(env):
    clock = Runtime()
    source = Source(clock, error="http_auth", retryable=False)
    run, _, _ = workflow(env, source, clock)
    assert run(env.query).stop_reason == "source_failed"
    assert run(env.query, retry_failed=True).stop_reason == "source_failed"
    assert len(source.calls) == 1


@pytest.mark.parametrize("mutation", ["missing", "corrupt"])
def test_unprocessed_missing_or_corrupt_raw_never_triggers_network(env, mutation):
    attempt, _ = prior.saved(env)
    digest = json.loads(attempt.capture_json)["response_sha256"]
    path = env.root / "objects/raw" / digest[:2] / digest
    if mutation == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupted fixture")
    source = Mock(side_effect=AssertionError("must not refetch"))
    run, _, _ = workflow(env, source)
    with pytest.raises(StorageError):
        run(env.query)
    source.assert_not_called()
    assert resume(env).checkpoint_version == 0


@pytest.mark.parametrize(
    "change",
    [
        {"lifecycle": "paused"},
        {"current_revision": 2},
        {"revision": 2},
        {"fingerprint": "f" * 64},
        {"domains": ()},
        {"scope_text": "different"},
        {"filters_json": '{"sources":[]}'},
    ],
)
def test_stale_paused_or_mismapped_profile_fails_before_effects(env, change):
    profile = replace(env.profiles("personal"), **change)
    source = Mock()
    run, _, _ = workflow(env, source, profiles=Mock(return_value=profile))
    with pytest.raises(HarvestWorkflowError):
        run(env.query)
    source.assert_not_called()
    assert count(env, "harvest_units") == 0


def test_pause_during_fetch_preserves_raw_but_stops_before_processing(env):
    clock = Runtime()
    source = Source(clock)
    source.after_fetch = lambda: SqliteWatchProfileStoreAdapter(env.factory.connect).set_lifecycle(
        "personal", "paused"
    )
    run, _, _ = workflow(env, source, clock)
    result = run(env.query)
    assert result.stop_reason == "profile_changed"
    assert result.fetch_count == 1 and result.processed_pages == 0
    assert count(env, "object_registry") == 1 and count(env, "source_observations") == 0
    SqliteWatchProfileStoreAdapter(env.factory.connect).set_lifecycle("personal", "active")
    source.after_fetch = None
    again = run(env.query, max_pages=1)
    assert again.reused_captures == 1 and len(source.calls) == 1


@pytest.mark.parametrize("max_pages", [0, -1, True, 1.0, 101, None])
def test_invalid_page_budget_is_rejected_before_ports(env, max_pages):
    profiles, source = Mock(), Mock()
    run, _, _ = workflow(env, source, profiles=profiles)
    with pytest.raises(HarvestWorkflowError, match="invalid_run_limit"):
        run(env.query, max_pages=max_pages)
    profiles.assert_not_called()
    source.assert_not_called()
    assert count(env, "harvest_units") == 0


def test_invalid_retry_type_is_rejected_before_ports(env):
    profiles = Mock()
    run, source, _ = workflow(env, profiles=profiles)
    with pytest.raises(HarvestWorkflowError, match="invalid_retry_policy"):
        run(env.query, retry_failed=1)
    profiles.assert_not_called()
    assert not source.calls


def test_corrupt_current_progress_fails_closed_instead_of_starting_again(env):
    workflow(env)[0](env.query, max_pages=1)
    prior.database(env, "UPDATE harvest_units SET cursor_json=NULL")
    source = Mock()
    run, _, _ = workflow(env, source)
    with pytest.raises(HarvestError):
        run(env.query)
    source.assert_not_called()


def test_binding_provenance_cannot_be_silently_replaced(env):
    prior.saved(env)
    prior.database(env, "UPDATE source_bindings SET compiled_query_json='{}'")
    with pytest.raises(HarvestError, match="binding_conflict"):
        resume(env)


def test_unit_without_attempt_is_not_treated_as_initial_state(env):
    prior.saved(env)
    prior.database(env, "DELETE FROM harvest_attempts")
    with pytest.raises(HarvestError, match="resume_attempt_missing"):
        resume(env)


def test_inconsistent_binding_without_unit_is_not_a_new_workspace(env):
    prior.saved(env)
    prior.database(env, "DELETE FROM harvest_attempts")
    prior.database(env, "DELETE FROM harvest_units")
    with pytest.raises(HarvestError, match="resume_unit_missing"):
        resume(env)


def test_failure_in_another_window_does_not_change_finished_window(env):
    clock = Runtime()
    good, _, _ = workflow(env, runtime=clock)
    good(env.query, max_pages=2)
    other = replace(env.query, window_start=NOW + timedelta(days=1), window_end=NOW + timedelta(days=2))
    bad, _, _ = workflow(env, Source(clock, error="http_timeout"), clock)
    failed = bad(other)
    assert failed.progress.state == "failed" and resume(env).state == "succeeded"
    assert failed.progress.unit_id != resume(env).unit_id


def test_sqlite_write_lock_is_not_held_during_fetch(env):
    clock = Runtime()
    source = Source(clock)

    def probe():
        connection = env.factory.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        finally:
            connection.close()

    source.after_fetch = probe
    assert workflow(env, source, clock)[0](env.query, max_pages=1).processed_pages == 1


@pytest.mark.parametrize("phase", ["capture", "checkpoint"])
def test_true_process_exit_resumes_without_repeating_durable_work(env, phase):
    code = """
import os, sys
from pathlib import Path
from libs.research_workflow.tests.contract.test_resumable_harvest import reopen, workflow
from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
e = reopen(Path(sys.argv[1]))
record = RecordHarvestCapture(e.journal, e.publish)
process = ProcessHarvestPage(ReadHarvestAttempt(e.journal), e.objects, e.parser, e.store, 'arxiv-atom-v1')
def crash_record(*args):
    record(*args)
    os._exit(23)
def crash_process(*args):
    process(*args)
    os._exit(23)
run, _, _ = workflow(e, record=crash_record if sys.argv[2]=='capture' else record,
                    processor=crash_process if sys.argv[2]=='checkpoint' else process)
run(e.query, max_pages=1)
raise AssertionError('crash hook not executed')
"""
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code, str(env.root), phase], capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 23, completed.stderr
    new = reopen(env.root)
    current = resume(new)
    assert current.checkpoint_version == (0 if phase == "capture" else 1)
    run, source, _ = workflow(new)
    result = run(new.query, max_pages=2)
    assert result.stop_reason == "complete" and [r.start for r in source.calls] == [2]
    assert count(new, "source_observations") == 3 and count(new, "harvest_attempts") == 2
