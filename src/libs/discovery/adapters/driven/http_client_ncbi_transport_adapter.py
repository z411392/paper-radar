import http.client
import math
import ssl
import time
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlsplit

from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


class HttpClientNcbiTransportAdapter:
    """One fixed NCBI HTTPS GET. No redirects, cookies, proxies or retries."""

    _TARGETS = {
        "pubmed": (
            "eutils.ncbi.nlm.nih.gov",
            frozenset(
                {
                    "/entrez/eutils/esearch.fcgi",
                    "/entrez/eutils/efetch.fcgi",
                }
            ),
        ),
        "pmc-oai": (
            "pmc.ncbi.nlm.nih.gov",
            frozenset({"/api/oai/v1/mh/"}),
        ),
    }

    def __init__(
        self,
        *,
        enabled: bool = False,
        timeout_seconds: float = 30.0,
        maximum_body_bytes: int = 8_000_000,
        user_agent: str = "paper-radar/0.1 (+https://github.com/z411392/paper-radar)",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            type(enabled) is not bool
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 120
            or type(maximum_body_bytes) is not int
            or not 1 <= maximum_body_bytes <= 8_000_000
            or not isinstance(user_agent, str)
            or not 1 <= len(user_agent) <= 256
            or any(ord(char) < 32 or ord(char) > 126 for char in user_agent)
        ):
            raise SourceFetchError("invalid_transport_configuration")
        self._enabled = enabled
        self._timeout = float(timeout_seconds)
        self._limit = maximum_body_bytes
        self._user_agent = user_agent
        self._monotonic = monotonic

    @classmethod
    def _target(cls, request: SourcePageRequest) -> tuple[str, str]:
        if not isinstance(request, SourcePageRequest) or request.method != "GET":
            raise SourceFetchError("invalid_page_request")
        config = cls._TARGETS.get(request.source_id)
        if config is None:
            raise SourceFetchError("unsupported_source")
        host, allowed_paths = config
        try:
            parsed = urlsplit(request.url)
            port = parsed.port
        except ValueError:
            raise SourceFetchError("invalid_page_request") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.path not in allowed_paths
            or not parsed.query
            or parsed.fragment
        ):
            raise SourceFetchError("invalid_page_request")
        target = parsed.path + "?" + parsed.query
        return host, target

    def get(self, request: SourcePageRequest) -> SourceHttpResponse:
        if not self._enabled:
            raise SourceFetchError("source_fetch_disabled")
        host, target = self._target(request)
        connection, response = None, None
        body = bytearray()
        captured: tuple[tuple[str, str], ...] = ()
        received = datetime.now(timezone.utc)
        try:
            deadline = self._monotonic() + self._timeout
            connection = http.client.HTTPSConnection(
                host,
                timeout=self._timeout,
                context=ssl.create_default_context(),
            )
            connection.request(
                "GET",
                target,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "application/json, application/xml;q=0.9, text/xml;q=0.8",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            received = datetime.now(timezone.utc)
            wanted = {
                "retry-after",
                "date",
                "content-type",
                "content-length",
                "content-encoding",
                "etag",
                "last-modified",
            }
            captured = tuple(
                (key.lower(), value)
                for key, value in response.getheaders()
                if key.lower() in wanted
            )
            if any(
                len(value) > 4096 or "" in value or "
" in value
                for _, value in captured
            ):
                return SourceHttpResponse(
                    response.status,
                    b"",
                    (),
                    received,
                    "invalid_response_headers",
                )
            encodings = [
                value.strip().lower()
                for key, value in captured
                if key == "content-encoding"
            ]
            if encodings and encodings != ["identity"]:
                return SourceHttpResponse(
                    response.status,
                    b"",
                    captured,
                    received,
                    "unsupported_content_encoding",
                )
            lengths = [value for key, value in captured if key == "content-length"]
            expected = None
            if lengths:
                if (
                    len(lengths) != 1
                    or not lengths[0].isascii()
                    or not lengths[0].isdigit()
                    or len(lengths[0]) > 20
                ):
                    return SourceHttpResponse(
                        response.status,
                        b"",
                        captured,
                        received,
                        "invalid_response_headers",
                    )
                expected = int(lengths[0])
            while True:
                if self._monotonic() > deadline:
                    return SourceHttpResponse(
                        response.status,
                        bytes(body),
                        captured,
                        received,
                        "source_timeout",
                    )
                chunk = response.read1(min(65536, self._limit + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > self._limit:
                    return SourceHttpResponse(
                        response.status,
                        bytes(body[: self._limit]),
                        captured,
                        received,
                        "response_too_large",
                    )
            error = (
                "response_incomplete"
                if expected is not None and expected != len(body)
                else None
            )
            return SourceHttpResponse(
                response.status,
                bytes(body),
                captured,
                received,
                error,
            )
        except TimeoutError as exc:
            if response is not None:
                return SourceHttpResponse(
                    response.status,
                    bytes(body),
                    captured,
                    received,
                    "source_timeout",
                )
            raise SourceFetchError("source_timeout", retryable=True) from exc
        except ssl.SSLError as exc:
            raise SourceFetchError("source_tls_error") from exc
        except http.client.IncompleteRead as exc:
            if response is not None:
                body.extend(exc.partial[: max(0, self._limit - len(body))])
                return SourceHttpResponse(
                    response.status,
                    bytes(body),
                    captured,
                    received,
                    "response_incomplete",
                )
            raise SourceFetchError("source_protocol_error", retryable=True) from exc
        except http.client.HTTPException as exc:
            raise SourceFetchError("source_protocol_error", retryable=True) from exc
        except OSError as exc:
            raise SourceFetchError("source_connection_error", retryable=True) from exc
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                if connection is not None:
                    connection.close()
