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
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceFetchError("invalid_rate_limit_state")
    number = float(value)
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


class _NcbiLease:
    def __init__(
        self,
        fd: int,
        clock: Callable[[], float],
        not_before: float,
    ) -> None:
        self.fd = fd
        self.clock = clock
        self.not_before = not_before
        self.active = True

    def defer(self, seconds: float) -> None:
        if not self.active:
            raise SourceFetchError("rate_limit_lease_closed")
        proposed = _number(self.clock()) + _number(seconds)
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
            count = os.write(self.fd, remaining)
            if count <= 0:
                raise OSError("incomplete rate state write")
            remaining = remaining[count:]
        os.ftruncate(self.fd, len(content))
        os.fsync(self.fd)


class PosixNcbiRateLimitAdapter:
    """One local NCBI limiter shared across workspaces.

    0.34s is deliberately below the no-key 3 requests/s ceiling;
    0.11s is deliberately below the default API-key 10 requests/s ceiling.
    """

    def __init__(
        self,
        path: Path,
        *,
        api_key_present: bool,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if type(api_key_present) is not bool:
            raise SourceFetchError("invalid_transport_configuration")
        self._path = path
        self._clock = clock
        self._interval = 0.11 if api_key_present else 0.34

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
                        or data["version"] != 1
                        or data["provider"] != "ncbi"
                        or len(raw) > 1024
                    ):
                        raise ValueError
                    not_before = _number(data["not_before"])
                except (ValueError, TypeError, UnicodeError) as exc:
                    raise SourceFetchError("invalid_rate_limit_state") from exc
            now = _number(self._clock())
            if not_before > now:
                raise SourceFetchError(
                    "provider_deferred",
                    retryable=True,
                    retry_after_seconds=not_before - now,
                )
            lease = _NcbiLease(fd, self._clock, not_before)
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
