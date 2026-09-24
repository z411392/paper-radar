"""Publish immutable raw body then envelope through kernel ports.

A receipt ID must still be journaled by the owning workflow (Task #94 S2).
This adapter does not make filesystem and a future page checkpoint atomic.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules as Rules
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture, CrossrefStoredCapture
from libs.discovery.dtos.crossref_page import CrossrefPageRequest
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError

if TYPE_CHECKING:
    from libs.kernel.ports.publish_object_port import PublishObjectPort
    from libs.kernel.ports.read_object_port import ReadObjectPort


class KernelCrossrefCaptureStoreAdapter:
    def __init__(self, publish: PublishObjectPort, read: ReadObjectPort) -> None:
        self._publish = publish
        self._read = read

    def _put(self, body: bytes) -> str:
        digest = Rules.sha(body)
        ref = self._publish(body, "raw", "application/octet-stream", "source-response")
        if (ref.object_id != "raw:"+digest or ref.state != "available"
                or ref.content_sha256 != digest or ref.byte_size != len(body)):
            raise CrossrefCaptureError("crossref_capture_publish_mismatch")
        return ref.object_id

    def save(self, request: CrossrefPageRequest, capture: CrossrefHttpCapture, *, attempt_key: str) -> str:
        Rules.request_shape(request)
        Rules.validate(capture)
        Rules.text(attempt_key, "invalid_crossref_capture_attempt")
        self._put(capture.body)
        return self._put(Rules.receipt_content(request, capture, attempt_key=attempt_key))

    def read(self, receipt_id: str) -> CrossrefStoredCapture:
        Rules.object_id(receipt_id)
        content = self._read(receipt_id)
        if not isinstance(content, bytes) or Rules.sha(content) != receipt_id[4:]:
            raise CrossrefCaptureError("crossref_capture_receipt_mismatch")
        payload = Rules.load_receipt(content)
        required = {"schema_version", "attempt_key", "request", "status", "headers", "headers_scope",
                    "received_at", "complete", "capture_error", "body_object_id", "body_sha256", "body_size",
                    "hash_scope"}
        try:
            if (set(payload) != required or type(payload["schema_version"]) is not int
                    or payload["schema_version"] != 1 or payload["headers_scope"] != Rules.HEADERS_SCOPE):
                raise ValueError("shape")
            request = CrossrefPageRequest(**payload["request"])
            Rules.request_shape(request)
            Rules.text(payload["attempt_key"], "invalid_crossref_capture_attempt")
            body_id = Rules.object_id(payload["body_object_id"])
            digest = Rules.digest(payload["body_sha256"])
            if (body_id != "raw:"+digest or type(payload["body_size"]) is not int
                    or not 0 <= payload["body_size"] <= Rules.MAX_BODY):
                raise ValueError("body metadata")
            if (not isinstance(payload["headers"], list)
                    or any(not isinstance(pair, list) or len(pair) != 2
                           or not all(isinstance(item, str) for item in pair)
                           for pair in payload["headers"])):
                raise ValueError("headers")
            headers = tuple(tuple(pair) for pair in payload["headers"])
            received = datetime.fromisoformat(payload["received_at"])
        except (TypeError, ValueError, KeyError, OverflowError):
            raise CrossrefCaptureError("invalid_crossref_capture_receipt") from None
        body = self._read(body_id)
        if not isinstance(body, bytes) or len(body) != payload["body_size"] or Rules.sha(body) != digest:
            raise CrossrefCaptureError("crossref_capture_body_mismatch")
        capture = CrossrefHttpCapture(payload["status"], headers, body, received,
                                      payload["complete"], payload["capture_error"])
        Rules.validate(capture)
        scope = "http_content_coded_body" if capture.complete else "http_content_coded_prefix"
        if payload["hash_scope"] != scope:
            raise CrossrefCaptureError("invalid_crossref_capture_receipt")
        return CrossrefStoredCapture(receipt_id, payload["attempt_key"], request, capture, body_id, digest)
