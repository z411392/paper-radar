"""Fixed-host, single-request transport. Persist content-coded bytes before decoding."""
import http.client
import math
import re
import ssl
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from urllib.parse import urlsplit

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules as Rules
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError


class HttpClientCrossrefTransportAdapter:
    HEADERS = frozenset({
        "date", "retry-after", "content-type", "content-length", "content-encoding", "transfer-encoding",
        "etag", "last-modified", "location", "x-rate-limit-limit", "x-rate-limit-interval",
        "x-rate-limit-type", "x-concurrency-limit",
    })

    def __init__(self, *, contact_email: str, enabled: bool = False, timeout_seconds: float = 30.0,
                 maximum_body_bytes: int = Rules.MAX_BODY,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        if (type(enabled) is not bool or type(timeout_seconds) not in (int, float)
                or not 0 < timeout_seconds <= 120 or not math.isfinite(timeout_seconds)
                or type(maximum_body_bytes) is not int or not 1 <= maximum_body_bytes <= Rules.MAX_BODY
                or not isinstance(contact_email, str) or len(contact_email) > 254
                or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact_email) is None
                or any(ord(c) < 33 or ord(c) > 126 for c in contact_email)):
            raise CrossrefCaptureError("invalid_crossref_transport_configuration")
        self._enabled = enabled
        self._contact = contact_email
        self._timeout = float(timeout_seconds)
        self._limit = maximum_body_bytes
        self._monotonic = monotonic

    def get(self, plan: CrossrefWindowPlan, request: CrossrefPageRequest) -> CrossrefHttpCapture:
        if not self._enabled:
            raise CrossrefCaptureError("crossref_transport_disabled")
        try:
            valid = CrossrefSourceAdapter().page(plan, request.cursor)
        except (CrossrefProtocolError, AttributeError):
            raise CrossrefCaptureError("invalid_crossref_capture_request") from None
        if valid != request:
            raise CrossrefCaptureError("invalid_crossref_capture_request")
        if plan.definition.contact_email != self._contact:
            raise CrossrefCaptureError("crossref_contact_mismatch")
        Rules.request_shape(request)
        target = urlsplit(request.url)
        body = bytearray()
        status = None
        headers = ()
        received = datetime.now(timezone.utc)
        conn = resp = None
        def captured(complete, error=None):
            return CrossrefHttpCapture(status, headers, bytes(body), received, complete, error)
        try:
            deadline = self._monotonic() + self._timeout
            context = ssl.create_default_context()
            context.set_alpn_protocols(["http/1.1"])
            conn = http.client.HTTPSConnection("api.crossref.org", timeout=self._timeout, context=context)
            conn.request("GET", target.path + "?" + target.query, headers={
                "User-Agent": f"PaperRadar/0.1 ({self._contact})",
                "Accept": "application/json", "Accept-Encoding": "gzip, deflate", "Connection": "close",
            })
            resp = conn.getresponse()
            status = resp.status
            received = datetime.now(timezone.utc)
            selected = [(k.lower(),v) for k,v in resp.getheaders() if k.lower() in self.HEADERS]
            headers = tuple((k,v[:4096]) for k,v in selected[:64])
            if len(selected)>64 or any(len(v)>4096 for _,v in selected):
                return captured(False, "invalid_response_headers")
            try:
                length = Rules.framing(headers)
            except CrossrefCaptureError:
                return captured(False, "invalid_response_framing")
            while True:
                if self._monotonic() > deadline:
                    return captured(False, "source_timeout")
                chunk = resp.read1(min(65536, self._limit + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > self._limit:
                    del body[self._limit:]
                    return captured(False, "response_too_large")
            if status not in {204, 304} and length is not None and len(body) != length:
                return captured(False, "response_incomplete")
            return captured(True)
        except http.client.IncompleteRead as exc:
            body.extend(exc.partial[:max(0,self._limit-len(body))])
            return captured(False, "response_incomplete")
        except TimeoutError:
            return captured(False, "source_timeout")
        except ssl.SSLError:
            return captured(False, "source_tls_error")
        except http.client.HTTPException:
            return captured(False, "source_protocol_error")
        except OSError:
            return captured(False, "source_connection_error")
        finally:
            # Never reuse connections. A cleanup error must not erase a captured
            # response or replace the original transport failure with no receipt.
            if resp is not None:
                with suppress(OSError):
                    resp.close()
            if conn is not None:
                with suppress(OSError):
                    conn.close()
