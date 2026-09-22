import hashlib
import re

from libs.discovery.adapters.driven.parse_retry_after import parse_retry_after
from libs.discovery.adapters.driven.validate_arxiv_page_request import validate_arxiv_page_request
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.ports.source_http_transport_port import SourceHttpTransportPort
from libs.discovery.ports.source_rate_limit_port import SourceRateLimitPort


class ArxivSourceAdapter:
    """One attempt and a retry directive. Raw bytes reach the durable caller first."""

    def __init__(
        self, transport: SourceHttpTransportPort, gate: SourceRateLimitPort, *, enabled: bool = False
    ) -> None:
        if type(enabled) is not bool:
            raise SourceFetchError("invalid_transport_configuration")
        self._transport, self._gate, self._enabled = transport, gate, enabled

    @staticmethod
    def _result(
        request: SourcePageRequest,
        response: SourceHttpResponse | None,
        code: str | None,
        retryable: bool = False,
        delay: float | None = None,
    ) -> SourceFetchResult:
        fingerprint = request.request_fingerprint if isinstance(request, SourcePageRequest) else ""
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            fingerprint = ""
        return SourceFetchResult(
            fingerprint,
            response,
            hashlib.sha256(response.body).hexdigest() if response is not None else None,
            code,
            retryable,
            delay,
        )

    def fetch(self, request: SourcePageRequest) -> SourceFetchResult:
        response = None
        try:
            if not self._enabled:
                raise SourceFetchError("source_fetch_disabled")
            validate_arxiv_page_request(request)
            with self._gate.slot() as lease:
                response = self._transport.get(request)
                status = response.status
                if status == 429 or 500 <= status < 600:
                    values = [value for key, value in response.headers if key.lower() == "retry-after"]
                    try:
                        if len(values) > 1:
                            raise SourceFetchError("invalid_retry_after")
                        delay = (
                            parse_retry_after(values[0], response.received_at)
                            if values
                            else (60.0 if status == 429 else 30.0)
                        )
                    except SourceFetchError:
                        lease.defer(60.0)
                        return self._result(request, response, "invalid_retry_after")
                    lease.defer(delay)
                    return self._result(
                        request,
                        response,
                        "source_rate_limited" if status == 429 else "source_server_error",
                        True,
                        delay,
                    )
                if response.capture_error is not None:
                    retry = response.capture_error in {"source_timeout", "response_incomplete"}
                    if retry:
                        lease.defer(3.0)
                    return self._result(
                        request, response, response.capture_error, retry, 3.0 if retry else None
                    )
                codes = {
                    304: "not_modified_without_cache",
                    401: "source_auth_required",
                    403: "source_forbidden",
                    404: "source_not_found",
                }
                if status != 200:
                    code = codes.get(
                        status, "source_redirect" if 300 <= status < 400 else "unexpected_http_status"
                    )
                    return self._result(request, response, code)
                encodings = [
                    value.strip().lower()
                    for key, value in response.headers
                    if key.lower() == "content-encoding"
                ]
                if encodings and encodings != ["identity"]:
                    return self._result(request, response, "unsupported_content_encoding")
                return self._result(request, response, None)
        except SourceFetchError as exc:
            return self._result(request, response, exc.code, exc.retryable, exc.retry_after_seconds)
