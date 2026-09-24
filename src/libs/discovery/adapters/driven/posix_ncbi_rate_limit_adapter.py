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
    if type(value) not in {int, float}:
        raise SourceFetchError("invalid_rate_limit_state")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise SourceFetchError("invalid_rate_limit_state") from None
    if not math.isfinite(result) or result < 0:
        raise SourceFetchError("invalid_rate_limit_state")
    return result


def _unique(rows: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in rows:
        if key in result:
            raise SourceFetchError("invalid_rate_limit_state")
        result[key] = value
    return result


class _NcbiLease:
    def __init__(
        self,
        fd: int,
        clock: Callable[[], float],
        interval: float,
        not_before: float,
    ) -> None:
        self.fd = fd
        self.clock = clock
        self.interval = interval
        self.not_before = not_before
        self.active = True

    def defer(self, seconds: float) -> None:
        if not self.active:
            raise SourceFetchError("rate_limit_lease_closed")
        proposed = _number(_number(self.clock()) + max(_number(seconds), self.interval))
        self.not_before = max(self.not_before, proposed)
        content = json.dumps(
            {
                "version": 1,
                "provider": "ncbi",
                "not_before": self.not_before,
            },
            allow_nan=False,
            separators=(",", ":"),
        ).encode("ascii")
        os.lseek(self.fd, 0, os.SEEK_SET)
        remaining = memoryview(content)
        while remaining:
            written = os.write(self.fd, remaining)
            if written <= 0:
                raise OSError("incomplete rate state write")
            remaining = remaining[written:]
        os.ftruncate(self.fd, len(content))
        os.fsync(self.fd)


class PosixNcbiRateLimitAdapter:
    """One stable inode shared by cooperating local NCBI consumers; never unlink it.

    Torn/invalid state is evidence requiring repair, not permission to reset the budget.
    This local lock is not a distributed rate limiter.
    """

    def __init__(
        self,
        path: Path,
        *,
        minimum_interval_seconds: float = 0.35,
        clock: Callable[[], float] = time.time,
    ) -> None:
        try:
            interval = _number(minimum_interval_seconds)
        except SourceFetchError:
            raise SourceFetchError("invalid_transport_configuration") from None
        if not 0.34 <= interval <= 60:
            raise SourceFetchError("invalid_transport_configuration")
        self._path = path
        self._interval = interval
        self._clock = clock

    @contextmanager
    def slot(self) -> Iterator[SourceRateLimitLeasePort]:
        fd = -1
        locked = False
        lease = None
        try:
            # Reject an invalid clock before creating or touching a provider state file.
            _number(self._clock())
            path = self._path.parent.resolve(strict=True) / self._path.name
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            created = False
            try:
                fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(path, flags)
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
            ):
                raise SourceFetchError("unsafe_rate_limit_path")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SourceFetchError(
                    "provider_busy",
                    retryable=True,
                    retry_after_seconds=self._interval,
                ) from exc
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
                        or data["provider"] != "ncbi"
                        or len(raw) > 1024
                    ):
                        raise ValueError
                    not_before = _number(data["not_before"])
                except (ValueError, TypeError, UnicodeError):
                    raise SourceFetchError("invalid_rate_limit_state") from None
            now = _number(self._clock())
            if not_before > now:
                raise SourceFetchError(
                    "provider_deferred",
                    retryable=True,
                    retry_after_seconds=not_before - now,
                )
            lease = _NcbiLease(fd, self._clock, self._interval, not_before)
            lease.defer(self._interval)
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
                    lease.defer(self._interval)
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
