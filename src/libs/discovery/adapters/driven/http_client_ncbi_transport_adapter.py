import http.client
import math
import re
import ssl
import time
import zlib
from collections.abc import Callable
from datetime import datetime, timezone

from libs.discovery.adapters.driven.validate_ncbi_page_request import (
    validate_ncbi_page_request,
)
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


class HttpClientNcbiTransportAdapter:
    """One bounded request, returning decoded entity bytes rather than compressed wire bytes.

    Wire length and encoding validate the transfer but are not attached to the decoded body.
    No redirects, retries, credential lookup or parser execution occur here. Socket timeouts
    and between-read checks are not a hard deadline for operating-system DNS resolution.
    """

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

    @staticmethod
    def _metadata(
        raw: list[tuple[str, str]],
    ) -> tuple[tuple[tuple[str, str], ...], int | None, str]:
        if len(raw) > 64 or any(
            len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value)
            for _, value in raw
        ):
            raise SourceFetchError("invalid_response_headers")
        headers = tuple((key.lower(), value) for key, value in raw)
        lengths = [value for key, value in headers if key == "content-length"]
        if len(lengths) > 1 or (lengths and re.fullmatch(r"[0-9]{1,20}", lengths[0]) is None):
            raise SourceFetchError("invalid_response_headers")
        transfers = [value.strip().lower() for key, value in headers if key == "transfer-encoding"]
        if transfers and (transfers != ["chunked"] or lengths):
            raise SourceFetchError("invalid_response_headers")
        encodings = [value.strip().lower() for key, value in headers if key == "content-encoding"]
        if len(encodings) > 1:
            raise SourceFetchError("unsupported_content_encoding")
        encoding = encodings[0] if encodings else "identity"
        if encoding not in {"identity", "gzip", "deflate"}:
            raise SourceFetchError("unsupported_content_encoding")
        retained = {"retry-after", "date", "content-type", "etag", "last-modified"}
        return (
            tuple((key, value) for key, value in headers if key in retained),
            int(lengths[0]) if lengths else None,
            encoding,
        )

    def get(self, request: SourcePageRequest) -> SourceHttpResponse:
        if not self._enabled:
            raise SourceFetchError("source_fetch_disabled")
        host, target = validate_ncbi_page_request(request)
        connection = response = None
        body = bytearray()
        headers: tuple[tuple[str, str], ...] = ()
        received = datetime.now(timezone.utc)

        def captured(code: str | None) -> SourceHttpResponse:
            assert response is not None
            return SourceHttpResponse(response.status, bytes(body), headers, received, code)

        try:
            deadline = self._monotonic() + self._timeout
            connection = http.client.HTTPSConnection(
                host, timeout=self._timeout, context=ssl.create_default_context(),
            )
            connection.request(
                "GET", target,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "application/json, application/xml, text/xml",
                    "Accept-Encoding": "gzip, deflate",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            received = datetime.now(timezone.utc)
            headers, expected_length, encoding = self._metadata(response.getheaders())
            if expected_length is not None and expected_length > self._limit:
                return captured("response_too_large")
            decoder = None
            if encoding != "identity":
                bits = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
                decoder = zlib.decompressobj(bits)
            wire_size = 0
            while True:
                if self._monotonic() > deadline:
                    return captured("source_timeout")
                chunk = response.read1(min(65536, self._limit + 1 - wire_size))
                if not chunk:
                    break
                wire_size += len(chunk)
                if wire_size > self._limit:
                    return captured("response_too_large")
                decoded = chunk if decoder is None else decoder.decompress(
                    chunk, self._limit + 1 - len(body),
                )
                too_large = len(body) + len(decoded) > self._limit
                body.extend(decoded[: self._limit - len(body)])
                if too_large or (decoder is not None and decoder.unconsumed_tail):
                    return captured("response_too_large")
                if decoder is not None and decoder.unused_data:
                    return captured("invalid_content_encoding")
            if expected_length is not None and wire_size != expected_length:
                return captured("response_incomplete")
            # flush() alone does not prove an end marker/checksum was received.
            if decoder is not None and not decoder.eof:
                return captured("response_incomplete")
            return captured(None)
        except SourceFetchError as exc:
            if response is not None:
                return captured(exc.code)
            raise
        except zlib.error:
            return captured("invalid_content_encoding")
        except TimeoutError as exc:
            if response is not None:
                return captured("source_timeout")
            raise SourceFetchError("source_timeout", retryable=True) from exc
        except ssl.SSLError as exc:
            raise SourceFetchError("source_tls_error") from exc
        except http.client.IncompleteRead as exc:
            # exc.partial is wire data: never append it to an already decoded prefix.
            if response is not None:
                return captured("response_incomplete")
            raise SourceFetchError("source_protocol_error", retryable=True) from exc
        except http.client.HTTPException as exc:
            if response is not None:
                return captured("response_incomplete")
            raise SourceFetchError("source_protocol_error", retryable=True) from exc
        except OSError as exc:
            if response is not None:
                return captured("response_incomplete")
            raise SourceFetchError("source_connection_error", retryable=True) from exc
        finally:
            try:
                if response is not None:
                    response.close()
            finally:
                if connection is not None:
                    connection.close()
