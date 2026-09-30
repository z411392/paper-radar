import fcntl
import json
import os
from unittest.mock import patch

import pytest

from libs.discovery.adapters.driven.posix_ncbi_rate_limit_adapter import PosixNcbiRateLimitAdapter
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


@pytest.mark.parametrize("raw", [
    '{"version":true,"provider":"ncbi","not_before":0}',
    '{"version":1,"provider":"ncbi","not_before":false}',
    '{"version":1,"provider":"ncbi","not_before":"0"}',
    '{"version":1,"provider":"ncbi","not_before":1000,"not_before":0}',
    '{"version":1,"provider":"ncbi","not_before":NaN}',
    '{"version":1,"provider":"ncbi","not_before":-1}',
    '{"version":1,"provider":"other","not_before":0}',
    '',
])
def test_corrupt_state_never_opens_a_slot_or_rewrites_evidence(tmp_path, raw):
    path = tmp_path / "ncbi.json"
    path.write_text(raw)
    with pytest.raises(SourceFetchError, match="invalid_rate_limit_state"):
        with PosixNcbiRateLimitAdapter(path, clock=lambda: 100.0).slot():
            pytest.fail("corrupt state allowed a request")
    assert path.read_text() == raw


@pytest.mark.parametrize("value", [True, "100", float("nan"), float("inf"), -1])
def test_invalid_clock_cannot_enter_a_provider_slot(tmp_path, value):
    path = tmp_path / "ncbi.json"
    path.write_text('{"version":1,"provider":"ncbi","not_before":0}')
    with pytest.raises(SourceFetchError, match="invalid_rate_limit_state"):
        with PosixNcbiRateLimitAdapter(path, clock=lambda: value).slot():
            pytest.fail("invalid clock allowed a request")


def test_only_one_local_caller_can_hold_the_shared_provider_slot(tmp_path):
    path = tmp_path / "ncbi.json"
    gate = PosixNcbiRateLimitAdapter(path, clock=lambda: 100.0)
    with gate.slot():
        with pytest.raises(SourceFetchError, match="provider_busy"):
            with PosixNcbiRateLimitAdapter(path, clock=lambda: 100.0).slot():
                pytest.fail("two owners held the provider lock")
    with pytest.raises(SourceFetchError, match="provider_deferred"):
        with gate.slot():
            pytest.fail("cooldown was ignored")
    with PosixNcbiRateLimitAdapter(path, clock=lambda: 101.0).slot():
        pass


def test_replaced_lock_inode_is_rejected_before_any_request(tmp_path):
    path = tmp_path / "ncbi.json"
    initial = '{"version":1,"provider":"ncbi","not_before":0}'
    path.write_text(initial)
    real_flock = fcntl.flock

    def replace_after_lock(fd, operation):
        real_flock(fd, operation)
        if operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
            replacement = tmp_path / "replacement"
            replacement.write_text(initial)
            os.replace(replacement, path)

    with patch("fcntl.flock", side_effect=replace_after_lock):
        with pytest.raises(SourceFetchError, match="unsafe_rate_limit_path"):
            with PosixNcbiRateLimitAdapter(path, clock=lambda: 100.0).slot():
                pytest.fail("request entered using an unlinked lock inode")
    assert path.read_text() == initial


def test_retry_after_persists_and_closed_lease_cannot_write(tmp_path):
    path = tmp_path / "ncbi.json"
    with PosixNcbiRateLimitAdapter(path, clock=lambda: 100.0).slot() as lease:
        lease.defer(20.0)
    assert json.loads(path.read_text())["not_before"] == 120.0
    with pytest.raises(SourceFetchError, match="rate_limit_lease_closed"):
        lease.defer(30.0)


def test_symlink_state_never_changes_the_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("do not change")
    path = tmp_path / "link"
    path.symlink_to(target)
    with pytest.raises(SourceFetchError):
        with PosixNcbiRateLimitAdapter(path, clock=lambda: 100.0).slot():
            pytest.fail("symlink allowed")
    assert target.read_text() == "do not change"
