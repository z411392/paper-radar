import fcntl
import json
import math
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.ports.source_rate_limit_port import SourceRateLimitLeasePort


def _number(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SourceFetchError("invalid_rate_limit_state")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise SourceFetchError("invalid_rate_limit_state") from exc
    if not math.isfinite(number) or number < 0:
        raise SourceFetchError("invalid_rate_limit_state")
    return number


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SourceFetchError("invalid_rate_limit_state")
        result[key] = value
    return result


class _FileLease:
    def __init__(self, fd: int, clock: Callable[[], float], not_before: float) -> None:
        self.fd, self.clock, self.not_before, self.active = fd, clock, not_before, True

    def defer(self, seconds: float) -> None:
        if not self.active:
            raise SourceFetchError("rate_limit_lease_closed")
        proposed = _number(self.clock()) + _number(seconds)
        self.not_before = max(self.not_before, _number(proposed))
        content = json.dumps(
            {"version": 1, "provider": "arxiv", "not_before": self.not_before}, allow_nan=False
        ).encode("ascii")
        os.lseek(self.fd, 0, os.SEEK_SET)
        remaining = memoryview(content)
        while remaining:
            count = os.write(self.fd, remaining)
            if count <= 0:
                raise OSError("incomplete rate state write")
            remaining = remaining[count:]
        os.ftruncate(self.fd, len(content))
        os.fsync(self.fd)


class PosixArxivRateLimitAdapter:
    """One stable lock inode for all local arXiv consumers; never unlink it.

    The caller provisions the parent and shares this path across workspaces.
    Invalid/crash-torn state fails closed. This is not a distributed limiter.
    """

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._path, self._clock = path, clock

    @contextmanager
    def slot(self) -> Iterator[SourceRateLimitLeasePort]:
        fd, locked, lease = -1, False, None
        try:
            path = self._path.parent.resolve(strict=True) / self._path.name
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            created = False
            try:
                fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(path, flags)
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.getuid():
                raise SourceFetchError("unsafe_rate_limit_path")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SourceFetchError("provider_busy", retryable=True, retry_after_seconds=3.0) from exc
            locked = True
            current = path.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise SourceFetchError("unsafe_rate_limit_path")
            raw = os.read(fd, 1025)
            if created and not raw:
                not_before = 0.0
            else:
                try:
                    data = json.loads(raw, object_pairs_hook=_unique)
                    if (
                        not isinstance(data, dict)
                        or set(data) != {"version", "provider", "not_before"}
                        or type(data["version"]) is not int
                        or data["version"] != 1
                        or data["provider"] != "arxiv"
                        or len(raw) > 1024
                    ):
                        raise ValueError
                    not_before = _number(data["not_before"])
                except (ValueError, TypeError, UnicodeError) as exc:
                    raise SourceFetchError("invalid_rate_limit_state") from exc
            now = _number(self._clock())
            if not_before > now:
                raise SourceFetchError(
                    "provider_deferred", retryable=True, retry_after_seconds=not_before - now
                )
            lease = _FileLease(fd, self._clock, not_before)
            lease.defer(3.0)
            if created:
                parent_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            try:
                yield lease
            finally:
                try:
                    lease.defer(3.0)
                finally:
                    lease.active = False
        except OSError as exc:
            raise SourceFetchError("rate_limit_io_error") from exc
        finally:
            if lease is not None:
                lease.active = False
            if fd >= 0:
                try:
                    if locked:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
