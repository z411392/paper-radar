"""Real POSIX file locks with synthetic time and no network."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import PosixArxivRateLimitAdapter
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


def test_construction_does_not_create_files(tmp_path: Path) -> None:
    path = tmp_path / "arxiv.lock"
    PosixArxivRateLimitAdapter(path)
    assert not path.exists()


def test_completion_and_reopen_keep_three_second_cooldown(tmp_path: Path) -> None:
    path, clock = tmp_path / "arxiv.lock", [1000.0]
    gate = PosixArxivRateLimitAdapter(path, clock=lambda: clock[0])
    with gate.slot():
        clock[0] = 1010.0
    other = PosixArxivRateLimitAdapter(path, clock=lambda: clock[0])
    with pytest.raises(SourceFetchError) as caught:
        with other.slot():
            pytest.fail("too early")
    assert caught.value.code == "provider_deferred" and caught.value.retry_after_seconds == 3
    clock[0] = 1013.0
    with other.slot():
        pass
    assert json.loads(path.read_text())["not_before"] == 1016.0


def test_two_gate_instances_cannot_hold_same_source_at_once(tmp_path: Path) -> None:
    path = tmp_path / "arxiv.lock"
    one, two = (PosixArxivRateLimitAdapter(path, clock=lambda: 1000.0) for _ in range(2))
    with one.slot():
        with pytest.raises(SourceFetchError, match="provider_busy"):
            with two.slot():
                pytest.fail("overlap")


def test_new_process_observes_lock_and_persisted_delay(tmp_path: Path) -> None:
    path = tmp_path / "arxiv.lock"
    script = '''
import sys
from pathlib import Path
from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import PosixArxivRateLimitAdapter
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
try:
    with PosixArxivRateLimitAdapter(Path(sys.argv[1]), clock=lambda: float(sys.argv[2])).slot():
        print("acquired")
except SourceFetchError as exc:
    print(exc.code)
'''
    def child(now):
        result = subprocess.run([sys.executable, "-c", script, str(path), str(now)], text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()
    with PosixArxivRateLimitAdapter(path, clock=lambda: 1000.0).slot():
        assert child(1000) == "provider_busy"
    assert child(1001) == "provider_deferred"
    assert child(1003) == "acquired"


def test_exception_releases_lock_without_removing_cooldown(tmp_path: Path) -> None:
    path = tmp_path / "arxiv.lock"
    with pytest.raises(RuntimeError):
        with PosixArxivRateLimitAdapter(path, clock=lambda: 1000.0).slot():
            raise RuntimeError("synthetic request failure")
    with pytest.raises(SourceFetchError, match="provider_deferred"):
        with PosixArxivRateLimitAdapter(path, clock=lambda: 1001.0).slot():
            pytest.fail("must not retry immediately")


def test_process_exit_keeps_reserved_cooldown(tmp_path: Path) -> None:
    path = tmp_path / "arxiv.lock"
    script = '''
import os,sys
from pathlib import Path
from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import PosixArxivRateLimitAdapter
with PosixArxivRateLimitAdapter(Path(sys.argv[1]), clock=lambda: 1000.0).slot():
    os._exit(17)
'''
    result = subprocess.run([sys.executable, "-c", script, str(path)], timeout=10)
    assert result.returncode == 17
    with pytest.raises(SourceFetchError, match="provider_deferred"):
        with PosixArxivRateLimitAdapter(path, clock=lambda: 1001.0).slot():
            pytest.fail("crash is not a rate-limit reset")


@pytest.mark.parametrize("raw", [b"", b"broken", b"{}", b"[]", b'{"version":1,"provider":"arxiv","not_before":NaN}', b'{"version":1,"provider":"arxiv","not_before":-1}', b'{"version":1,"provider":"arxiv","not_before":0,"not_before":1}', b'{"version":1,"provider":"pubmed","not_before":0}', b"x" * 1025])
def test_existing_bad_state_is_preserved_and_rejected(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "arxiv.lock"
    path.write_bytes(raw)
    with pytest.raises(SourceFetchError, match="invalid_rate_limit_state"):
        with PosixArxivRateLimitAdapter(path, clock=lambda: 1000.0).slot():
            pytest.fail("must fail closed")
    assert path.read_bytes() == raw


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory"])
def test_unsafe_files_are_not_written(tmp_path: Path, kind: str) -> None:
    target, path = tmp_path / "private", tmp_path / "arxiv.lock"
    target.write_bytes(b"do not change")
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(SourceFetchError):
        with PosixArxivRateLimitAdapter(path).slot():
            pytest.fail("unsafe lock")
    assert target.read_bytes() == b"do not change"


def test_retry_after_only_extends_and_stale_lease_cannot_write(tmp_path: Path) -> None:
    path = tmp_path / "arxiv.lock"
    with PosixArxivRateLimitAdapter(path, clock=lambda: 1000.0).slot() as lease:
        lease.defer(120)
        lease.defer(3)
    assert json.loads(path.read_text())["not_before"] == 1120.0
    before = path.read_bytes()
    with pytest.raises(SourceFetchError, match="rate_limit_lease_closed"):
        lease.defer(9999)
    assert path.read_bytes() == before


def test_clock_going_backwards_is_conservative(tmp_path: Path) -> None:
    path = tmp_path / "arxiv.lock"
    with PosixArxivRateLimitAdapter(path, clock=lambda: 1000.0).slot():
        pass
    with pytest.raises(SourceFetchError) as caught:
        with PosixArxivRateLimitAdapter(path, clock=lambda: 900.0).slot():
            pytest.fail("clock moved backwards")
    assert caught.value.retry_after_seconds == 103.0
