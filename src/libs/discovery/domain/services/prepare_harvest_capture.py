import hashlib
import json
import math
import re
from datetime import datetime, timezone

from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.prepared_harvest_capture import PreparedHarvestCapture
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.exceptions.harvest_error import HarvestError


class PrepareHarvestCapture:
    """Pure validation; never publish bytes or infer parse/coverage success."""

    HEADERS = frozenset({"content-type", "content-length", "retry-after", "etag", "last-modified", "date"})

    @staticmethod
    def time(value: datetime) -> str:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise HarvestError("invalid_harvest_time")
        try:
            return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
        except (ValueError, OverflowError) as exc:
            raise HarvestError("invalid_harvest_time") from exc

    @staticmethod
    def code(value: str | None) -> None:
        if value is not None and (
            not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value) is None
        ):
            raise HarvestError("invalid_capture_code")

    def __call__(
        self, attempt: HarvestAttempt, result: SourceFetchResult, recorded_at: datetime
    ) -> PreparedHarvestCapture:
        end = self.time(recorded_at)
        if end < attempt.started_at:
            raise HarvestError("invalid_harvest_time")
        if (
            not isinstance(result, SourceFetchResult)
            or result.request_fingerprint != attempt.request.request_fingerprint
        ):
            raise HarvestError("capture_request_mismatch")
        self.code(result.failure_code)
        if type(result.retryable) is not bool:
            raise HarvestError("invalid_capture_retry")
        delay = result.retry_after_seconds
        if delay is not None:
            try:
                valid_delay = type(delay) in (int, float) and math.isfinite(delay) and delay >= 0
            except OverflowError:
                valid_delay = False
            if not valid_delay:
                raise HarvestError("invalid_capture_retry")
        response = result.response
        if response is None:
            if result.response_sha256 is not None or result.failure_code is None:
                raise HarvestError("invalid_capture_response")
            raw_id, body = None, None
            details = {
                "status": None,
                "headers": [],
                "received_at": None,
                "byte_size": None,
                "body_complete": None,
                "capture_error": None,
                "hash_scope": "no_response",
            }
        else:
            if (
                not isinstance(response, SourceHttpResponse)
                or type(response.status) is not int
                or not 100 <= response.status <= 599
            ):
                raise HarvestError("invalid_capture_response")
            self.code(response.capture_error)
            if not isinstance(response.body, bytes) or len(response.body) > 8_000_000:
                raise HarvestError("invalid_capture_body")
            received = self.time(response.received_at)
            if received < attempt.started_at or received > end:
                raise HarvestError("invalid_harvest_time")
            if result.failure_code is None and (
                response.status != 200 or not response.body_complete or result.retryable or delay is not None
            ):
                raise HarvestError("invalid_capture_success")
            body = response.body
            digest = hashlib.sha256(body).hexdigest()
            if digest != result.response_sha256:
                raise HarvestError("capture_hash_mismatch")
            raw_id = "raw:" + digest
            if not isinstance(response.headers, tuple) or len(response.headers) > 32:
                raise HarvestError("invalid_capture_headers")
            for pair in response.headers:
                if not isinstance(pair, tuple) or len(pair) != 2:
                    raise HarvestError("invalid_capture_headers")
                name, value = pair
                if (
                    not isinstance(name, str)
                    or name.lower() not in self.HEADERS
                    or not isinstance(value, str)
                ):
                    raise HarvestError("invalid_capture_headers")
                try:
                    size = len(value.encode("utf-8"))
                except UnicodeError as exc:
                    raise HarvestError("invalid_capture_headers") from exc
                if size > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                    raise HarvestError("invalid_capture_headers")
            details = {
                "status": response.status,
                "headers": list(response.headers),
                "received_at": received,
                "byte_size": len(body),
                "body_complete": response.body_complete,
                "capture_error": response.capture_error,
                "hash_scope": "complete_body" if response.body_complete else "captured_prefix",
            }
        data = {
            "format_version": 1,
            "request_fingerprint": result.request_fingerprint,
            "raw_object_id": raw_id,
            "response_sha256": result.response_sha256,
            "failure_code": result.failure_code,
            "retryable": result.retryable,
            "retry_after_seconds": delay,
            **details,
        }
        try:
            text = json.dumps(
                data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            if len(text.encode("utf-8")) > 65536:
                raise HarvestError("capture_metadata_too_large")
        except (ValueError, UnicodeError) as exc:
            raise HarvestError("invalid_capture_metadata") from exc
        if attempt.capture_json is not None and attempt.capture_json != text:
            raise HarvestError("capture_conflict")
        return PreparedHarvestCapture(text, raw_id, body)
