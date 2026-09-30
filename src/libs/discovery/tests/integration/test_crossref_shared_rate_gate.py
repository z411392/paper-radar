"""Real POSIX file locks and durable state; no network or production workspace."""

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.posix_crossref_rate_gate_adapter import (
    PosixCrossrefRateGateAdapter,
)
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError

EMAIL = "operator@example.org"


class Clock:
    def __init__(self, now=1000.0):
        self.value = now

    def __call__(self):
        return self.value


@pytest.fixture
def setup(tmp_path):
    directory = tmp_path / "shared-provider-budget"
    directory.mkdir(mode=0o700)
    clock = Clock()
    gate = PosixCrossrefRateGateAdapter(directory, EMAIL, clock=clock, random_value=lambda: 1.0)
    return directory, clock, gate


def test_construction_does_not_create_state_and_identity_is_shared(setup):
    directory, clock, gate = setup
    other = PosixCrossrefRateGateAdapter(directory, "OPERATOR@EXAMPLE.ORG", clock=clock)
    assert gate.state_path == other.state_path
    assert list(directory.iterdir()) == []
    with gate.slot(EMAIL):
        pass
    assert EMAIL not in gate.state_path.read_text()
    assert gate.state_path.stat().st_mode & 0o777 == 0o600


def test_same_identity_second_worker_is_denied_while_first_holds_slot(setup):
    directory, clock, gate = setup
    other = PosixCrossrefRateGateAdapter(directory, EMAIL, clock=clock)
    with gate.slot(EMAIL):
        with pytest.raises(CrossrefRateError, match="crossref_provider_busy"):
            with other.slot(EMAIL):
                pytest.fail("second admission")


def test_429_budget_survives_reopen_and_rejects_until_due(setup):
    directory, clock, gate = setup
    with gate.slot(EMAIL) as lease:
        decision = lease.observe(429, (("retry-after", "60"),))
        assert decision.action == "retry"
    reopened = PosixCrossrefRateGateAdapter(directory, EMAIL, clock=clock)
    clock.value = 1059
    with pytest.raises(CrossrefRateError, match="crossref_provider_deferred") as error:
        with reopened.slot(EMAIL):
            pytest.fail("early admission")
    assert error.value.retry_after_seconds == 1
    clock.value = 1060
    with reopened.slot(EMAIL):
        pass


def test_403_circuit_is_persistent_and_not_reset_by_reconstruction_or_time(setup):
    directory, clock, gate = setup
    with gate.slot(EMAIL) as lease:
        lease.observe(403, ())
    clock.value += 86400
    restarted = PosixCrossrefRateGateAdapter(directory, EMAIL, clock=clock)
    with pytest.raises(CrossrefRateError, match="crossref_circuit_open"):
        with restarted.slot(EMAIL):
            pytest.fail("blocked contact admitted")
    assert json.loads(gate.state_path.read_text())["blocked"] is True


def test_backoff_failure_count_survives_restart_and_success_resets_only_failure_count(setup):
    directory, clock, gate = setup
    with gate.slot(EMAIL) as lease:
        assert lease.observe(503, ()).delay_seconds == 2
    clock.value += 2
    reopened = PosixCrossrefRateGateAdapter(directory, EMAIL, clock=clock, random_value=lambda: 1.0)
    with reopened.slot(EMAIL) as lease:
        assert lease.observe(503, ()).delay_seconds == 4
    clock.value += 4
    with reopened.slot(EMAIL) as lease:
        lease.observe(200, ())
    assert json.loads(gate.state_path.read_text())["failures"] == 0
    with pytest.raises(CrossrefRateError, match="crossref_provider_deferred"):
        with reopened.slot(EMAIL):
            pytest.fail("success must not erase interval")


def test_slow_provider_limit_persists_and_new_faster_header_cannot_erase_it(setup):
    _, clock, gate = setup
    with gate.slot(EMAIL) as lease:
        lease.observe(200, (("x-rate-limit-limit", "1"), ("x-rate-limit-interval", "5s")))
    clock.value += 5
    with gate.slot(EMAIL) as lease:
        lease.observe(200, (("x-rate-limit-limit", "10"), ("x-rate-limit-interval", "1s")))
    assert json.loads(gate.state_path.read_text())["interval_seconds"] == 5


def test_exception_consumes_slot_and_closed_lease_cannot_change_state(setup):
    _, _, gate = setup
    with pytest.raises(RuntimeError, match="fake failure"):
        with gate.slot(EMAIL) as lease:
            raise RuntimeError("fake failure")
    before = gate.state_path.read_bytes()
    with pytest.raises(CrossrefRateError, match="crossref_lease_closed"):
        lease.observe(200, ())
    assert gate.state_path.read_bytes() == before
    with pytest.raises(CrossrefRateError, match="crossref_provider_deferred"):
        with gate.slot(EMAIL):
            pytest.fail("slot was reset on error")


@pytest.mark.parametrize("key,value", [
    ("not_before", True), ("not_before", "1001"), ("not_before", float("nan")),
    ("interval_seconds", 0), ("interval_seconds", False), ("failures", True),
    ("failures", -1), ("blocked", "false"), ("version", True), ("identity", "wrong"),
])
def test_corrupt_state_is_not_reset_or_rewritten(setup, key, value):
    _, clock, gate = setup
    with gate.slot(EMAIL):
        pass
    payload = json.loads(gate.state_path.read_text())
    payload[key] = value
    gate.state_path.write_text(json.dumps(payload))
    before = gate.state_path.read_bytes()
    clock.value += 10
    with pytest.raises(CrossrefRateError, match="crossref_rate_state_corrupt"):
        with gate.slot(EMAIL):
            pytest.fail("corrupt state admitted")
    assert gate.state_path.read_bytes() == before


def test_duplicate_json_key_is_rejected_without_reset(setup):
    _, clock, gate = setup
    with gate.slot(EMAIL):
        pass
    content = gate.state_path.read_text().rstrip("}") + ',"blocked":false}'
    gate.state_path.write_text(content)
    clock.value += 10
    with pytest.raises(CrossrefRateError, match="crossref_rate_state_corrupt"):
        with gate.slot(EMAIL):
            pytest.fail("duplicate key")
    assert gate.state_path.read_text() == content


def test_replaced_inode_does_not_leave_authorized_stale_lease(setup):
    directory, _, gate = setup
    with pytest.raises(CrossrefRateError, match="crossref_rate_path_changed"):
        with gate.slot(EMAIL) as lease:
            moved = directory / "moved.json"
            gate.state_path.rename(moved)
            gate.state_path.write_text("replacement")
            lease.observe(403, ())
    assert gate.state_path.read_text() == "replacement"


def test_contact_mismatch_fails_before_state_creation(setup):
    directory, _, gate = setup
    with pytest.raises(CrossrefRateError, match="crossref_contact_mismatch"):
        with gate.slot("other@example.org"):
            pytest.fail("wrong pool")
    assert list(directory.iterdir()) == []


def test_clock_regression_never_loosens_last_durable_deadline(setup):
    _, clock, gate = setup
    with gate.slot(EMAIL):
        pass
    before = gate.state_path.read_bytes()
    clock.value -= 100
    with pytest.raises(CrossrefRateError, match="crossref_clock_regressed"):
        with gate.slot(EMAIL):
            pytest.fail("time regression")
    assert gate.state_path.read_bytes() == before


def test_insecure_directory_is_rejected(setup):
    directory, _, gate = setup
    directory.chmod(0o777)
    with pytest.raises(CrossrefRateError, match="crossref_rate_path_unsafe"):
        with gate.slot(EMAIL):
            pytest.fail("untrusted directory")


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "world_writable"])
def test_unsafe_state_file_is_rejected(setup, kind):
    directory, clock, gate = setup
    with gate.slot(EMAIL):
        pass
    clock.value += 5
    if kind == "symlink":
        target = directory / "target.json"
        gate.state_path.rename(target)
        gate.state_path.symlink_to(target)
    elif kind == "hardlink":
        os.link(gate.state_path, directory / "second-link.json")
    else:
        gate.state_path.chmod(0o666)
    with pytest.raises(CrossrefRateError):
        with gate.slot(EMAIL):
            pytest.fail("unsafe state admitted")


def _competing_worker(directory, gate_event, results):
    gate_event.wait(5)
    budget = PosixCrossrefRateGateAdapter(Path(directory), EMAIL, clock=lambda: 1000.0)
    try:
        with budget.slot(EMAIL):
            results.put("admitted")
    except CrossrefRateError as error:
        results.put(error.code)


def test_independent_processes_admit_only_one_request(setup):
    directory, _, _ = setup
    ctx = multiprocessing.get_context("spawn")
    event = ctx.Event()
    results = ctx.Queue()
    children = [ctx.Process(target=_competing_worker, args=(str(directory), event, results)) for _ in range(2)]
    for child in children:
        child.start()
    event.set()
    received = [results.get(timeout=10) for _ in children]
    for child in children:
        child.join(timeout=10)
        assert child.exitcode == 0
    assert received.count("admitted") == 1
    assert set(received) <= {"admitted", "crossref_provider_busy", "crossref_provider_deferred"}
