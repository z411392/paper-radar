"""Single-flight Crossref budget shared by contact, outside workspace SQL transactions.

Every worker must use the same private local directory. This is not a distributed
limiter. State corruption or a clock rollback requires operator intervention.
"""

import fcntl
import hashlib
import json
import math
import os
import random
import re
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
from libs.discovery.ports.crossref_rate_gate_port import CrossrefRateLeasePort


def _contact_identity(email: str) -> str:
    if (
        not isinstance(email, str) or len(email) > 254
        or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email, flags=re.ASCII) is None
        or any(ord(char) < 33 or ord(char) > 126 for char in email)
    ):
        raise CrossrefRateError("invalid_crossref_contact")
    # Conservative collision: case variants share a budget; the outgoing mailto is not rewritten.
    return hashlib.sha256(("crossref-polite\0" + email.lower()).encode("ascii")).hexdigest()


def _number(value: object, lower: float, upper: float) -> bool:
    return type(value) in (int, float) and lower <= value <= upper and math.isfinite(value)


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def _decode(raw: bytes, identity: str) -> dict:
    def pairs(items):
        data = {}
        for key, value in items:
            if key in data:
                raise ValueError("duplicate key")
            data[key] = value
        return data

    try:
        data = json.loads(raw.decode("ascii"), object_pairs_hook=pairs)
        if (
            not isinstance(data, dict)
            or set(data) != {
                "version", "identity", "not_before", "last_now", "interval_seconds", "failures", "blocked",
            }
            or type(data["version"]) is not int or data["version"] != 1
            or data["identity"] != identity
            or not _number(data["not_before"], 0, 253402300799)
            or not _number(data["last_now"], 0, data["not_before"])
            or not _number(data["interval_seconds"], 1, CrossrefRatePolicy.MAX_WAIT)
            or type(data["failures"]) is not int or not 0 <= data["failures"] <= 1_000_000
            or type(data["blocked"]) is not bool
        ):
            raise ValueError("shape")
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        raise CrossrefRateError("crossref_rate_state_corrupt") from None
    return data


class _Lease:
    def __init__(self, gate, directory_fd: int, fd: int, state: dict) -> None:
        self.gate = gate
        self.directory_fd = directory_fd
        self.fd = fd
        self.state = state
        self.active = True
        self.observed = False

    def _check_path(self) -> None:
        current = os.stat(self.gate.state_path.name, dir_fd=self.directory_fd, follow_symlinks=False)
        opened = os.fstat(self.fd)
        directory = os.stat(self.gate.directory, follow_symlinks=False)
        held_directory = os.fstat(self.directory_fd)
        if (
            (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            or (directory.st_dev, directory.st_ino) != (held_directory.st_dev, held_directory.st_ino)
            or directory.st_uid != os.getuid() or directory.st_mode & 0o022
            or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
            or current.st_mode & 0o077 or current.st_uid != os.getuid()
        ):
            raise CrossrefRateError("crossref_rate_path_changed")

    def _now(self) -> float:
        value = self.gate.clock()
        if not _number(value, 0, 253399622399):
            raise CrossrefRateError("invalid_crossref_clock")
        if value < self.state["last_now"]:
            raise CrossrefRateError("crossref_clock_regressed")
        return float(value)

    def persist(self) -> None:
        self._check_path()
        content = _canonical(self.state)
        os.lseek(self.fd, 0, os.SEEK_SET)
        remaining = memoryview(content)
        while remaining:
            count = os.write(self.fd, remaining)
            if count <= 0:
                raise OSError("short state write")
            remaining = remaining[count:]
        os.ftruncate(self.fd, len(content))
        os.fsync(self.fd)
        self._check_path()

    def observe(
        self,
        status: int | None,
        headers: tuple[tuple[str, str], ...],
        *,
        capture_error: str | None = None,
    ) -> CrossrefRateDecision:
        if not self.active:
            raise CrossrefRateError("crossref_lease_closed")
        if self.observed:
            raise CrossrefRateError("crossref_response_already_recorded")
        self._check_path()
        now = self._now()
        decision = CrossrefRatePolicy().evaluate(
            status, headers, now=datetime.fromtimestamp(now, timezone.utc),
            failures=self.state["failures"], jitter=self.gate.random_value(), capture_error=capture_error,
        )
        self.state["interval_seconds"] = max(
            self.state["interval_seconds"], decision.minimum_interval_seconds,
        )
        self.state["not_before"] = max(
            self.state["not_before"], now + self.state["interval_seconds"], now + decision.delay_seconds,
        )
        self.state["last_now"] = now
        self.state["blocked"] = self.state["blocked"] or decision.open_circuit
        if decision.action == "retry":
            self.state["failures"] = min(1_000_000, self.state["failures"] + 1)
        elif decision.action == "accept":
            self.state["failures"] = 0
        self.persist()
        self.observed = True
        return decision

    def finish(self) -> None:
        try:
            now = self._now()
            self.state["last_now"] = now
            self.state["not_before"] = max(self.state["not_before"], now + self.state["interval_seconds"])
            self.persist()
        finally:
            self.active = False


class PosixCrossrefRateGateAdapter:
    def __init__(
        self,
        directory: Path,
        contact_email: str,
        *,
        clock: Callable[[], float] = time.time,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if not isinstance(directory, Path) or not directory.is_absolute():
            raise CrossrefRateError("invalid_crossref_rate_directory")
        self.directory = directory
        self.identity = _contact_identity(contact_email)
        self.state_path = directory / ("crossref-" + self.identity + ".json")
        self.clock = clock
        self.random_value = random_value

    @contextmanager
    def slot(self, contact_email: str) -> Iterator[CrossrefRateLeasePort]:
        if _contact_identity(contact_email) != self.identity:
            raise CrossrefRateError("crossref_contact_mismatch")
        directory_fd = fd = -1
        locked = directory_locked = False
        lease = None
        try:
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directory = os.fstat(directory_fd)
            if directory.st_uid != os.getuid() or directory.st_mode & 0o022:
                raise CrossrefRateError("crossref_rate_path_unsafe")
            # Serialize the empty-file creation window, not the subsequent network request.
            try:
                fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CrossrefRateError("crossref_provider_busy", 1.0) from None
            directory_locked = True
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            created = False
            try:
                fd = os.open(self.state_path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd)
                created = True
            except FileExistsError:
                fd = os.open(self.state_path.name, flags, dir_fd=directory_fd)
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077
            ):
                raise CrossrefRateError("crossref_rate_path_unsafe")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CrossrefRateError("crossref_provider_busy", 1.0) from None
            locked = True
            raw = os.read(fd, 4097)
            if len(raw) > 4096:
                raise CrossrefRateError("crossref_rate_state_corrupt")
            state = ({
                "version": 1, "identity": self.identity, "not_before": 0.0,
                "last_now": 0.0, "interval_seconds": 1.0, "failures": 0, "blocked": False,
            } if created and not raw else _decode(raw, self.identity))
            lease = _Lease(self, directory_fd, fd, state)
            lease._check_path()
            now = lease._now()
            if state["blocked"]:
                raise CrossrefRateError("crossref_circuit_open")
            if state["not_before"] > now:
                raise CrossrefRateError("crossref_provider_deferred", state["not_before"] - now)
            state["last_now"] = now
            state["not_before"] = now + state["interval_seconds"]
            lease.persist()  # Reserve before caller can perform any network I/O.
            if created:
                os.fsync(directory_fd)
            fcntl.flock(directory_fd, fcntl.LOCK_UN)
            directory_locked = False
            try:
                yield lease
            finally:
                lease.finish()
        except OSError as exc:
            raise CrossrefRateError("crossref_rate_io_error") from exc
        finally:
            if lease is not None:
                lease.active = False
            if fd >= 0:
                try:
                    if locked:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            if directory_fd >= 0:
                try:
                    if directory_locked:
                        fcntl.flock(directory_fd, fcntl.LOCK_UN)
                finally:
                    os.close(directory_fd)
