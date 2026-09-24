"""Crossref operational decisions, not provider content or checkpoint semantics."""

import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError


class CrossrefRatePolicy:
    LOCAL_INTERVAL = 1.0  # Conservative local ceiling; not a provider guarantee.
    MAX_WAIT = 31 * 86400
    RETRYABLE_CAPTURE_ERRORS = frozenset({
        "source_timeout", "source_connection_error", "response_incomplete", "source_protocol_error",
    })

    @staticmethod
    def _headers(headers: tuple[tuple[str, str], ...]) -> dict[str, list[str]]:
        if not isinstance(headers, tuple) or len(headers) > 64:
            raise CrossrefRateError("invalid_crossref_headers")
        result: dict[str, list[str]] = {}
        for pair in headers:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise CrossrefRateError("invalid_crossref_headers")
            key, value = pair
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", key) is None
                or not isinstance(value, str)
                or len(value) > 4096
                or any(ord(char) < 32 or ord(char) > 126 for char in value)
            ):
                raise CrossrefRateError("invalid_crossref_headers")
            result.setdefault(key.lower(), []).append(value.strip())
        return result

    @staticmethod
    def _one(headers: dict[str, list[str]], key: str) -> str | None:
        values = headers.get(key)
        if values is None:
            return None
        if len(values) != 1:
            raise ValueError("duplicate header")
        return values[0]

    @staticmethod
    def _positive_integer(value: str | None, maximum: int) -> int:
        if value is None or re.fullmatch(r"[0-9]{1,9}", value) is None:
            raise ValueError("invalid integer")
        number = int(value)
        if not 1 <= number <= maximum:
            raise ValueError("invalid integer")
        return number

    @classmethod
    def _limits(cls, headers: dict[str, list[str]]) -> tuple[float, int | None, str | None, list[str]]:
        warnings: list[str] = []
        interval = cls.LOCAL_INTERVAL
        if not ({"x-rate-limit-limit", "x-rate-limit-interval"} & headers.keys()):
            warnings.append("crossref_rate_headers_missing")
        else:
            try:
                limit = cls._positive_integer(cls._one(headers, "x-rate-limit-limit"), 1_000_000)
                period = cls._one(headers, "x-rate-limit-interval")
                if period is None or re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,6})?s", period) is None:
                    raise ValueError("invalid interval")
                seconds = float(period[:-1])
                if not 0 < seconds <= cls.MAX_WAIT:
                    raise ValueError("invalid interval")
                interval = max(interval, seconds / limit)
            except ValueError:
                warnings.append("crossref_rate_headers_invalid")
        concurrency = None
        if "x-concurrency-limit" in headers:
            try:
                concurrency = cls._positive_integer(cls._one(headers, "x-concurrency-limit"), 1024)
            except ValueError:
                warnings.append("crossref_concurrency_header_invalid")
        rate_type = None
        try:
            value = cls._one(headers, "x-rate-limit-type")
            if value is not None:
                if not value or len(value) > 64:
                    raise ValueError("invalid type")
                rate_type = value  # Open vocabulary; never select a faster bucket from this string.
        except ValueError:
            warnings.append("crossref_rate_type_invalid")
        return interval, concurrency, rate_type, warnings

    @staticmethod
    def _http_date(value: str) -> datetime:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)

    @classmethod
    def _retry_after(cls, headers: dict[str, list[str]], now: datetime) -> float:
        value = cls._one(headers, "retry-after")
        if value is None:
            return 0.0
        if re.fullmatch(r"[0-9]{1,10}", value):
            delay = float(value)
        else:
            target = cls._http_date(value)
            delay = max(0.0, (target - now).total_seconds())
            # A valid server Date can only extend a wait, never shorten it.
            try:
                server_date = cls._one(headers, "date")
                if server_date is not None:
                    delay = max(delay, (target - cls._http_date(server_date)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
        if not math.isfinite(delay) or not 0 <= delay <= cls.MAX_WAIT:
            raise ValueError("unbounded wait")
        return delay

    def evaluate(
        self,
        status: int | None,
        headers: tuple[tuple[str, str], ...],
        *,
        now: datetime,
        failures: int = 0,
        jitter: float = 0.5,
        capture_error: str | None = None,
    ) -> CrossrefRateDecision:
        if (
            (status is not None and (type(status) is not int or not 100 <= status <= 599))
            or type(failures) is not int or not 0 <= failures <= 1_000_000
            or type(jitter) not in (int, float) or not 0 <= jitter <= 1 or not math.isfinite(jitter)
            or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None
            or (capture_error is not None and (
                not isinstance(capture_error, str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", capture_error) is None
            ))
        ):
            raise CrossrefRateError("invalid_crossref_rate_input")
        try:
            values = self._headers(headers)
        except CrossrefRateError:
            if status != 403:
                raise
            return CrossrefRateDecision(
                "stop", "crossref_forbidden", 0.0, self.LOCAL_INTERVAL, None, None,
                ("crossref_response_headers_invalid",), True,
            )
        interval, concurrency, rate_type, warnings = self._limits(values)
        common = (interval, concurrency, rate_type, tuple(warnings))
        if status == 403:
            return CrossrefRateDecision("stop", "crossref_forbidden", 0.0, *common, True)
        retryable = (
            status == 429 or (status is not None and status >= 500)
            or (status in (None, 200) and capture_error in self.RETRYABLE_CAPTURE_ERRORS)
        )
        if retryable:
            try:
                provider_delay = self._retry_after(values, now)
            except (ValueError, TypeError, OverflowError):
                return CrossrefRateDecision("stop", "crossref_retry_after_invalid", 0.0, *common, True)
            base = min(300.0, 2.0 ** min(failures + 1, 9))
            delay = max(interval, provider_delay, base * (0.5 + 0.5 * jitter))
            code = (
                "crossref_rate_limited" if status == 429 else
                "crossref_server_error" if status is not None and status >= 500 else capture_error
            )
            return CrossrefRateDecision("retry", code, delay, *common)
        if status == 200 and capture_error is None:
            return CrossrefRateDecision("accept", None, 0.0, *common)
        code = capture_error or (
            "crossref_redirect" if status is not None and 300 <= status < 400 else
            "crossref_http_error" if status is not None else "crossref_no_response"
        )
        return CrossrefRateDecision("stop", code, 0.0, *common)
