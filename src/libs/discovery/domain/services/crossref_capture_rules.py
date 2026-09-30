"""Bounded receipt values and content decoding, with separate byte/hash scopes."""
import hashlib
import json
import re
import zlib
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlsplit

from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefPageRequest
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError


class CrossrefCaptureRules:
    MAX_BODY = 8_000_000
    MAX_RECEIPT = 2_000_000
    HEADERS_SCOPE = "selected_bounded_operational_headers"

    @staticmethod
    def text(value: object, code: str, maximum: int = 256) -> str:
        if (not isinstance(value, str) or not value.strip() or len(value) > maximum
                or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise CrossrefCaptureError(code)
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise CrossrefCaptureError(code) from None
        return value

    @staticmethod
    def digest(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise CrossrefCaptureError("invalid_crossref_capture_hash")
        return value

    @classmethod
    def object_id(cls, value: object) -> str:
        if not isinstance(value, str) or not value.startswith("raw:"):
            raise CrossrefCaptureError("invalid_crossref_capture_object")
        cls.digest(value[4:])
        return value

    @staticmethod
    def instant(value: object) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CrossrefCaptureError("invalid_crossref_capture_time")
        try:
            if value.utcoffset() is None:
                raise ValueError
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise CrossrefCaptureError("invalid_crossref_capture_time") from None

    @classmethod
    def request_shape(cls, request: CrossrefPageRequest) -> None:
        if not isinstance(request, CrossrefPageRequest):
            raise CrossrefCaptureError("invalid_crossref_capture_request")
        for value in (request.query_fingerprint, request.parameters_fingerprint, request.request_fingerprint):
            cls.digest(value)
        cls.text(request.cursor, "invalid_crossref_capture_request", 65536)
        cls.text(request.url, "invalid_crossref_capture_request", 300000)
        try:
            url = urlsplit(request.url)
            safe = (url.scheme == "https" and url.netloc == "api.crossref.org"
                    and url.path == "/works" and not url.fragment)
        except ValueError:
            safe = False
        if not safe:
            raise CrossrefCaptureError("invalid_crossref_capture_request")

    @classmethod
    def validate(cls, capture: CrossrefHttpCapture) -> None:
        if not isinstance(capture, CrossrefHttpCapture):
            raise CrossrefCaptureError("invalid_crossref_capture")
        if capture.status is not None and (
            type(capture.status) is not int or not 100 <= capture.status <= 599
        ):
            raise CrossrefCaptureError("invalid_crossref_capture_status")
        if not isinstance(capture.body, bytes) or len(capture.body) > cls.MAX_BODY:
            raise CrossrefCaptureError("invalid_crossref_capture_body")
        cls.instant(capture.received_at)
        if type(capture.complete) is not bool:
            raise CrossrefCaptureError("invalid_crossref_capture_complete")
        if capture.complete:
            if capture.status is None or capture.capture_error is not None:
                raise CrossrefCaptureError("invalid_crossref_capture_complete")
        elif (not isinstance(capture.capture_error, str)
              or re.fullmatch(r"[a-z][a-z0-9_]{0,79}", capture.capture_error) is None):
            raise CrossrefCaptureError("invalid_crossref_capture_error")
        if capture.status is None and (capture.body or capture.headers):
            raise CrossrefCaptureError("invalid_crossref_no_response")
        if not isinstance(capture.headers, tuple) or len(capture.headers) > 64:
            raise CrossrefCaptureError("invalid_crossref_capture_headers")
        for pair in capture.headers:
            if (not isinstance(pair, tuple) or len(pair) != 2
                    or not all(isinstance(v, str) for v in pair)
                    or re.fullmatch(r"[a-z0-9-]{1,128}", pair[0]) is None or len(pair[1]) > 4096):
                raise CrossrefCaptureError("invalid_crossref_capture_headers")
            # Malformed bounded values remain evidence; JSON serialization prevents markup execution.
            try:
                pair[1].encode("utf-8")
            except UnicodeError:
                raise CrossrefCaptureError("invalid_crossref_capture_headers") from None

    @staticmethod
    def canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                          allow_nan=False).encode("ascii")

    @classmethod
    def load_receipt(cls, content: bytes) -> dict:
        if not isinstance(content, bytes) or not content or len(content) > cls.MAX_RECEIPT:
            raise CrossrefCaptureError("invalid_crossref_capture_receipt")
        def pairs(items):
            obj = {}
            for k, v in items:
                if k in obj:
                    raise ValueError("duplicate key")
                obj[k] = v
            return obj
        def constant(_):
            raise ValueError("nonfinite")
        try:
            data = json.loads(content.decode("ascii"), object_pairs_hook=pairs, parse_constant=constant)
            if not isinstance(data, dict):
                raise ValueError("shape")
        except (ValueError, UnicodeError, RecursionError):
            raise CrossrefCaptureError("invalid_crossref_capture_receipt") from None
        return data

    @staticmethod
    def framing(headers: tuple[tuple[str, str], ...]) -> int | None:
        lengths = [v.strip() for k,v in headers if k == "content-length"]
        transfers = [v.strip().lower() for k,v in headers if k == "transfer-encoding"]
        if (len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
                or (transfers and transfers != ["chunked"])):
            raise CrossrefCaptureError("invalid_response_framing")
        if lengths:
            if re.fullmatch(r"[0-9]{1,18}", lengths[0]) is None:
                raise CrossrefCaptureError("invalid_response_framing")
            return int(lengths[0])
        return None

    @classmethod
    def entity(cls, capture: CrossrefHttpCapture, *, maximum_bytes: int = MAX_BODY) -> bytes:
        cls.validate(capture)
        if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= cls.MAX_BODY:
            raise CrossrefCaptureError("invalid_crossref_entity_limit")
        if not capture.complete or capture.status != 200:
            raise CrossrefCaptureError("crossref_capture_not_parseable")
        length = cls.framing(capture.headers)
        if length is not None and length != len(capture.body):
            raise CrossrefCaptureError("crossref_capture_body_mismatch")
        encodings = [v.strip().lower() for k,v in capture.headers if k == "content-encoding"]
        if len(encodings) > 1 or (encodings and encodings[0] not in {"identity", "gzip", "deflate"}):
            raise CrossrefCaptureError("crossref_content_encoding_unsupported")
        encoding = encodings[0] if encodings else "identity"
        if encoding == "identity":
            data = capture.body
        else:
            try:
                decoder = zlib.decompressobj(16+zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
                data = decoder.decompress(capture.body, maximum_bytes+1)
                if len(data) > maximum_bytes or decoder.unconsumed_tail:
                    raise CrossrefCaptureError("crossref_entity_too_large")
                # v1 intentionally rejects multi-member gzip, raw-deflate fallback, and trailing data.
                if not decoder.eof or decoder.unused_data:
                    raise CrossrefCaptureError("crossref_content_decoding_failed")
            except zlib.error:
                raise CrossrefCaptureError("crossref_content_decoding_failed") from None
        if len(data) > maximum_bytes:
            raise CrossrefCaptureError("crossref_entity_too_large")
        return data

    @staticmethod
    def sha(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @classmethod
    def receipt_content(
        cls, request: CrossrefPageRequest, capture: CrossrefHttpCapture, *, attempt_key: str,
    ) -> bytes:
        """Canonical v1 receipt bytes shared by publication and attachment verification."""
        cls.request_shape(request)
        cls.validate(capture)
        cls.text(attempt_key, "invalid_crossref_capture_attempt")
        payload = {
            "schema_version": 1, "attempt_key": attempt_key, "request": asdict(request),
            "status": capture.status, "headers": capture.headers,
            "headers_scope": cls.HEADERS_SCOPE,
            "received_at": cls.instant(capture.received_at).isoformat(),
            "complete": capture.complete, "capture_error": capture.capture_error,
            "body_object_id": "raw:" + cls.sha(capture.body),
            "body_sha256": cls.sha(capture.body), "body_size": len(capture.body),
            "hash_scope": "http_content_coded_body" if capture.complete else "http_content_coded_prefix",
        }
        content = cls.canonical(payload)
        if len(content) > cls.MAX_RECEIPT:
            raise CrossrefCaptureError("crossref_capture_receipt_too_large")
        return content
