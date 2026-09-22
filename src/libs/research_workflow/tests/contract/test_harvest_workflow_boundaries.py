from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock

import pytest

from libs.discovery.adapters.driven.arxiv_source_adapter import ArxivSourceAdapter
from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import PosixArxivRateLimitAdapter
from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.application.queries.fetch_source_page import FetchSourcePage
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.research_workflow.tests.contract import test_resumable_harvest as base


@pytest.fixture
def env(tmp_path):
    return base.env.__wrapped__(tmp_path)


@pytest.mark.parametrize(
    "domains",
    [
        (("statistics", True),),
        (("statistics", 1.0),),
        (("statistics", 1), ("statistics", 1)),
        None,
    ],
)
def test_malformed_profile_domain_references_fail_before_effects(env, domains):
    profile = replace(env.profiles("personal"), domains=domains)
    source = Mock()
    run, _, _ = base.workflow(env, source, profiles=Mock(return_value=profile))
    with pytest.raises(HarvestWorkflowError):
        run(env.query, max_pages=1)
    source.assert_not_called()
    assert base.count(env, "harvest_attempts") == 0


def test_duplicate_filter_keys_are_not_silently_resolved_by_last_value(env):
    profile = env.profiles("personal")
    duplicate = '{"sources":[], ' + profile.filters_json[1:]
    source = Mock()
    run, _, _ = base.workflow(
        env, source, profiles=Mock(return_value=replace(profile, filters_json=duplicate))
    )
    with pytest.raises(HarvestWorkflowError):
        run(env.query, max_pages=1)
    source.assert_not_called()
    assert base.count(env, "harvest_attempts") == 0


class Transport:
    def __init__(self, clock):
        self.source = base.Source(clock)
        self.rate_limit_once = False

    def get(self, request):
        response = self.source(request).response
        assert response is not None
        if self.rate_limit_once:
            self.rate_limit_once = False
            return replace(response, status=429, headers=response.headers + (("retry-after", "7"),))
        return response


def connected(env, clock):
    # Real local gate and source classification; only the remote transport is synthetic.
    transport = Transport(clock)
    gate = PosixArxivRateLimitAdapter(
        env.root.parent / "arxiv-shared-gate.json", clock=lambda: clock.now().timestamp()
    )
    fetch = FetchSourcePage(ArxivSourceAdapter(transport, gate, enabled=True))
    return base.workflow(env, fetch, clock)[0], transport, gate


def test_real_source_gate_stops_tight_loop_and_reopens_after_cooldown(env):
    clock = base.Runtime()
    run, transport, _ = connected(env, clock)
    first = run(env.query, max_pages=2)
    assert first.stop_reason == "source_failed" and first.progress.next_start == 2
    assert first.progress.previous_result.error_code == "provider_deferred"
    assert len(transport.source.calls) == 1
    waiting = run(env.query, retry_failed=True)
    assert waiting.stop_reason == "cooldown" and waiting.fetch_count == 0
    clock.value += timedelta(seconds=3)
    done = run(env.query, max_pages=1, retry_failed=True)
    assert done.stop_reason == "complete" and done.progress.next_start == 3
    assert [r.start for r in transport.source.calls] == [0, 2]
    assert base.count(env, "harvest_attempts") == 3
    assert base.count(env, "source_observations") == 3


def test_real_source_gate_is_shared_across_independent_query_windows(env):
    clock = base.Runtime()
    run, transport, _ = connected(env, clock)
    first = run(env.query, max_pages=1)
    other = replace(
        env.query, window_start=base.NOW + timedelta(days=1), window_end=base.NOW + timedelta(days=2)
    )
    second = run(other, max_pages=1)
    assert second.progress.unit_id != first.progress.unit_id
    assert second.progress.next_start == 0 and second.progress.state == "failed"
    assert second.progress.previous_result.error_code == "provider_deferred"
    assert len(transport.source.calls) == 1
    assert base.resume(env).next_start == 2


def test_held_real_gate_records_busy_without_touching_transport(env):
    clock = base.Runtime()
    run, transport, gate = connected(env, clock)
    with gate.slot():
        blocked = run(env.query, max_pages=1)
    assert blocked.progress.previous_result.error_code == "provider_busy"
    assert blocked.progress.checkpoint_version == 0 and not transport.source.calls
    clock.value += timedelta(seconds=3)
    retried = run(env.query, max_pages=1, retry_failed=True)
    assert retried.progress.next_start == 2 and len(transport.source.calls) == 1


def test_real_source_429_retry_after_is_respected_by_both_layers(env):
    clock = base.Runtime()
    run, transport, _ = connected(env, clock)
    transport.rate_limit_once = True
    limited = run(env.query, max_pages=1)
    assert limited.progress.previous_result.error_code == "source_rate_limited"
    assert base.count(env, "object_registry") == 1
    clock.value += timedelta(seconds=6)
    assert run(env.query, retry_failed=True).stop_reason == "cooldown"
    assert len(transport.source.calls) == 1
    clock.value += timedelta(seconds=1)
    resumed = run(env.query, max_pages=1, retry_failed=True)
    assert resumed.progress.next_start == 2 and len(transport.source.calls) == 2


def test_two_workflows_keep_only_one_committed_page_under_contention(env):
    clock = base.Runtime()
    rendezvous = Barrier(2, timeout=15)
    record = RecordHarvestCapture(env.journal, env.publish)

    def recorded(*args):
        result = record(*args)
        rendezvous.wait()
        return result

    def execute():
        run, _, _ = base.workflow(env, runtime=clock, record=recorded)
        try:
            return run(env.query, max_pages=1).stop_reason
        except HarvestError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert sorted(results) == ["checkpoint_conflict", "page_budget"]
    assert base.count(env, "source_observations") == 2
    assert base.count(env, "harvest_attempts") == 2
    assert base.resume(env).checkpoint_version == 1
    # Concurrent GETs may both occur; neither becomes a duplicate committed observation.
    finished = base.workflow(base.reopen(env.root))[0](env.query, max_pages=1)
    assert finished.stop_reason == "complete" and base.count(env, "source_observations") == 3


def test_unexpected_transport_exception_preserves_running_attempt(env):
    transport = Mock(side_effect=RuntimeError("synthetic adapter defect"))
    run, _, _ = base.workflow(env, transport)
    with pytest.raises(RuntimeError, match="synthetic adapter defect"):
        run(env.query, max_pages=1)
    assert base.count(env, "harvest_attempts") == 1
    assert base.count(env, "object_registry") == base.count(env, "source_observations") == 0
    state = base.resume(env)
    assert state.checkpoint_version == 0 and state.attempt.state == "running"


def test_new_parser_version_replays_saved_parse_failure_without_fetch(env):
    clock = base.Runtime()
    source = base.Source(clock)
    bad_parser = Mock()
    from libs.discovery.adapters.driven.sqlite_harvest_resume_adapter import SqliteHarvestResumeAdapter
    from libs.discovery.application.commands.process_harvest_page import ProcessHarvestPage
    from libs.discovery.application.commands.start_harvest_attempt import StartHarvestAttempt
    from libs.discovery.application.queries.read_harvest_attempt import ReadHarvestAttempt
    from libs.discovery.application.queries.read_harvest_resume import ReadHarvestResume
    from libs.discovery.exceptions.source_parse_error import SourceParseError
    from libs.research_workflow.application.commands.run_harvest_slice import RunHarvestSlice

    bad_parser.side_effect = SourceParseError("synthetic_parser_defect")
    broken = ProcessHarvestPage(
        ReadHarvestAttempt(env.journal), env.objects, bad_parser, env.store, "arxiv-atom-broken-test"
    )
    broken_run = RunHarvestSlice(
        env.compiler,
        ReadHarvestResume(SqliteHarvestResumeAdapter(env.factory.connect, env.compiler)),
        StartHarvestAttempt(env.journal),
        source,
        RecordHarvestCapture(env.journal, env.publish),
        broken,
        env.profiles,
        clock,
        "arxiv-atom-broken-test",
    )
    assert broken_run(env.query, max_pages=1).stop_reason == "processing_failed"
    repaired, _, _ = base.workflow(env, source, clock)
    result = repaired(env.query, max_pages=1)
    assert result.reused_captures == 1 and result.fetch_count == 0
    assert result.progress.next_start == 2 and len(source.calls) == 1
