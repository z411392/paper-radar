"""Regressions added after the first 77 tests passed; retain the initial oracles."""

import json
import os

import pytest

from libs.discovery.adapters.driven.posix_crossref_rate_gate_adapter import PosixCrossrefRateGateAdapter
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
from libs.discovery.tests.integration.test_crossref_shared_rate_gate import EMAIL
from libs.discovery.tests.integration.test_crossref_shared_rate_gate import setup as setup
from libs.discovery.tests.unit.test_crossref_rate_policy import decide


def test_403_with_invalid_header_syntax_still_opens_circuit():
    outcome = decide(403, (("bad\nname", "value"),))
    assert outcome.open_circuit
    assert outcome.failure_code == "crossref_forbidden"
    assert "crossref_response_headers_invalid" in outcome.warnings


def test_huge_integer_jitter_fails_with_stable_error_not_overflow():
    with pytest.raises(CrossrefRateError, match="invalid_crossref_rate_input"):
        decide(jitter=10**400)


def test_same_lease_cannot_observe_success_after_a_block(setup):
    _, _, gate = setup
    with gate.slot(EMAIL) as lease:
        lease.observe(403, ())
        with pytest.raises(CrossrefRateError, match="crossref_response_already_recorded"):
            lease.observe(200, ())
    assert json.loads(gate.state_path.read_text())["blocked"] is True


def test_new_state_initialization_race_is_busy_not_corruption(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    directory, clock, gate = setup
    created = Event()
    resume = Event()
    real_open = os.open

    def paused_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if path == gate.state_path.name and flags & os.O_EXCL:
            created.set()
            if not resume.wait(5):
                os.close(fd)
                raise RuntimeError("test release timeout")
        return fd

    monkeypatch.setattr(os, "open", paused_open)

    def first_worker():
        with gate.slot(EMAIL):
            return "admitted"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_worker)
        try:
            assert created.wait(5)
            other = PosixCrossrefRateGateAdapter(directory, EMAIL, clock=clock)
            with pytest.raises(CrossrefRateError, match="crossref_provider_busy"):
                with other.slot(EMAIL):
                    pytest.fail("initializing file must not be inspected as corrupt")
        finally:
            resume.set()
        assert future.result(timeout=5) == "admitted"
