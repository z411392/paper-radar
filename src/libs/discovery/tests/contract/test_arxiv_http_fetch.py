"""Synthetic HTTP responses only; these cases do not contact arXiv."""

import hashlib
import http.client
import json
import socket
import ssl
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.arxiv_source_adapter import ArxivSourceAdapter
from libs.discovery.adapters.driven.http_client_arxiv_transport_adapter import HttpClientArxivTransportAdapter
from libs.discovery.adapters.driven.posix_arxiv_rate_limit_adapter import PosixArxivRateLimitAdapter
from libs.discovery.application.queries.fetch_source_page import FetchSourcePage
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def request(
    url: str = "https://export.arxiv.org/api/query?search_query=cat%3Acs.SE&start=0&max_results=2&sortBy=submittedDate&sortOrder=ascending",
) -> SourcePageRequest:
    query = "a" * 64
    encoded = json.dumps(
        {"query": query, "method": "GET", "url": url},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return SourcePageRequest(
        "arxiv", query, hashlib.sha256(encoded.encode()).hexdigest(), "GET", url, 0, 2, 30000
    )


class Gate:
    def __init__(self) -> None:
        self.entries = 0
        self.delays: list[float] = []
        self.active = False

    @contextmanager
    def slot(self):
        self.entries += 1
        self.active = True
        try:
            yield self
        finally:
            self.active = False

    def defer(self, seconds: float) -> None:
        self.delays.append(seconds)


class Transport:
    def __init__(self, response: SourceHttpResponse | Exception, gate=None) -> None:
        self.response = response
        self.calls = 0
        self.gate = gate

    def get(self, page):
        self.calls += 1
        if self.gate is not None:
            assert self.gate.active
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def run(response, *, enabled=True):
    gate = Gate()
    transport = Transport(response, gate)
    result = FetchSourcePage(ArxivSourceAdapter(transport, gate, enabled=enabled))(request())
    return result, gate, transport


def test_raw_200_is_not_an_atom_success_and_is_not_printed() -> None:
    raw = b"<error>PRIVATE SOURCE CONTENT</error>"
    response = SourceHttpResponse(200, raw, (), NOW)
    result, gate, transport = run(response)
    assert result.failure_code is None
    assert result.response is response
    assert result.response_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.request_fingerprint == request().request_fingerprint
    assert "PRIVATE SOURCE CONTENT" not in repr(result)
    assert transport.calls == gate.entries == 1
    assert not gate.active


def test_disabled_fetch_does_not_touch_gate_or_transport() -> None:
    result, gate, transport = run(SourceHttpResponse(200, b"", (), NOW), enabled=False)
    assert result.failure_code == "source_fetch_disabled"
    assert gate.entries == transport.calls == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://export.arxiv.org/api/query?search_query=x",
        "https://localhost/api/query?search_query=x",
        "https://export.arxiv.org.evil.invalid/api/query?search_query=x",
        "https://user@export.arxiv.org/api/query?search_query=x",
        "https://export.arxiv.org:443/api/query?search_query=x",
        "https://export.arxiv.org/api/query?search_query=x#fragment",
        "https://export.arxiv.org/api/../query?search_query=x",
        "https://export.arxiv.org/api/query?search_query=x\n",
        "https://export.arxiv.org/api/query?search_query=x&start=0&start=1",
        "https://export.arxiv.org/api/query?search_query=x&start=1&max_results=2&sortBy=submittedDate&sortOrder=ascending",
        "https://export.arxiv.org/api/query?search_query=x&start=0&max_results=2&sortBy=submittedDate&sortOrder=ascending&api_key=private",
    ],
)
def test_invalid_request_never_acquires_or_fetches(url: str) -> None:
    gate, transport = Gate(), Transport(SourceHttpResponse(200, b"", (), NOW))
    result = ArxivSourceAdapter(transport, gate, enabled=True).fetch(request(url))
    assert result.failure_code == "invalid_source_request"
    assert gate.entries == transport.calls == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_fingerprint", "0" * 64),
        ("query_fingerprint", "x"),
        ("source_id", "pubmed"),
        ("method", "POST"),
        ("max_results", True),
        ("max_results", 2001),
        ("start", -1),
        ("maximum_window_results", 30001),
    ],
)
def test_request_metadata_is_verified(field, value) -> None:
    gate, transport = Gate(), Transport(SourceHttpResponse(200, b"", (), NOW))
    result = ArxivSourceAdapter(transport, gate, enabled=True).fetch(replace(request(), **{field: value}))
    assert result.failure_code == "invalid_source_request"
    assert not gate.entries and not transport.calls


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (301, "source_redirect", False),
        (302, "source_redirect", False),
        (307, "source_redirect", False),
        (308, "source_redirect", False),
        (304, "not_modified_without_cache", False),
        (401, "source_auth_required", False),
        (403, "source_forbidden", False),
        (404, "source_not_found", False),
        (429, "source_rate_limited", True),
        (500, "source_server_error", True),
        (503, "source_server_error", True),
        (204, "unexpected_http_status", False),
    ],
)
def test_http_failures_keep_raw_evidence_without_retrying(status, code, retryable) -> None:
    response = SourceHttpResponse(status, b"raw evidence", (), NOW)
    result, gate, transport = run(response)
    assert (result.failure_code, result.retryable) == (code, retryable)
    assert result.response is response and transport.calls == 1
    assert bool(gate.delays) == retryable


@pytest.mark.parametrize(
    "header,delay",
    [
        ("120", 120),
        ("0", 3),
        ("Wed, 23 Sep 2026 00:02:00 GMT", 120),
        ("Wednesday, 23-Sep-26 00:02:00 GMT", 120),
        ("Wed Sep 23 00:02:00 2026", 120),
        ("Tue, 22 Sep 2026 23:00:00 GMT", 3),
        ("999999999999999999999999", 1e24),
    ],
)
def test_retry_after_applies_to_the_shared_gate(header, delay) -> None:
    result, gate, transport = run(SourceHttpResponse(429, b"limited", (("retry-after", header),), NOW))
    assert result.retry_after_seconds == pytest.approx(delay)
    assert gate.delays == [pytest.approx(delay)]
    assert transport.calls == 1


@pytest.mark.parametrize(
    "header", ["-1", "+2", "1.5", "NaN", "tomorrow", "Wed, 23 Sep 2026 00:02:00 +0100", "9" * 300]
)
def test_invalid_retry_after_fails_closed(header) -> None:
    result, gate, _ = run(SourceHttpResponse(429, b"limited", (("retry-after", header),), NOW))
    assert result.failure_code == "invalid_retry_after"
    assert not result.retryable
    assert gate.delays == [60.0]


def test_duplicate_retry_after_is_not_arbitrarily_selected() -> None:
    result, _, _ = run(SourceHttpResponse(429, b"", (("retry-after", "1"), ("Retry-After", "3600")), NOW))
    assert result.failure_code == "invalid_retry_after" and not result.retryable


@pytest.mark.parametrize("code", ["source_timeout", "source_connection_error", "source_tls_error"])
def test_network_error_is_not_empty_success(code) -> None:
    result, gate, transport = run(SourceFetchError(code, retryable=code != "source_tls_error"))
    assert result.failure_code == code and result.response is None
    assert transport.calls == 1
    assert not gate.active


def test_throttle_remains_in_effect_after_429_and_new_adapter(tmp_path: Path) -> None:
    time_value = [1000.0]
    path = tmp_path / "arxiv.lock"
    gate = PosixArxivRateLimitAdapter(path, clock=lambda: time_value[0])
    first = Transport(SourceHttpResponse(429, b"limited", (("retry-after", "120"),), NOW))
    assert (
        ArxivSourceAdapter(first, gate, enabled=True).fetch(request()).failure_code == "source_rate_limited"
    )
    second = Transport(SourceHttpResponse(200, b"ok", (), NOW))
    other = ArxivSourceAdapter(
        second, PosixArxivRateLimitAdapter(path, clock=lambda: time_value[0]), enabled=True
    )
    time_value[0] = 1004.0
    result = other.fetch(request())
    assert result.failure_code == "provider_deferred" and result.retry_after_seconds >= 116
    assert second.calls == 0
    time_value[0] = 1120.0
    assert other.fetch(request()).failure_code is None
    assert second.calls == 1


class Response:
    def __init__(self, status=200, chunks=None, headers=None):
        self.status, self.chunks, self.headers = status, list(chunks or [b"<feed/>", b""]), headers or []
        self.closed = False

    def getheaders(self):
        return self.headers

    def read1(self, size):
        item = self.chunks.pop(0) if self.chunks else b""
        if isinstance(item, Exception):
            raise item
        assert len(item) <= size
        return item

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, response, error=None):
        self.response, self.error, self.closed, self.requests = response, error, False, []

    def request(self, method, target, *, headers):
        self.requests.append((method, target, headers))
        if self.error:
            raise self.error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def transport_with(monkeypatch, response, error=None, **kwargs):
    connection = Connection(response, error)
    calls = []

    def factory(host, *, timeout, context):
        calls.append((host, timeout, context))
        return connection

    monkeypatch.setattr(http.client, "HTTPSConnection", factory)
    adapter = HttpClientArxivTransportAdapter(enabled=True, **kwargs)
    return adapter, connection, calls


def test_transport_get_uses_fixed_host_and_closes_before_return(monkeypatch) -> None:
    response = Response(
        headers=[
            ("Content-Type", "application/atom+xml"),
            ("Set-Cookie", "SECRET"),
            ("Authorization", "SECRET"),
        ]
    )
    adapter, connection, calls = transport_with(monkeypatch, response)
    monkeypatch.setenv("HTTPS_PROXY", "http://localhost:1")
    result = adapter.get(request())
    assert result.status == 200 and result.body == b"<feed/>" and result.body_complete
    assert connection.closed and response.closed
    assert len(calls) == len(connection.requests) == 1
    assert calls[0][0] == "export.arxiv.org"
    assert "SECRET" not in repr(result)
    assert result.headers == (("content-type", "application/atom+xml"),)
    assert connection.requests[0][0] == "GET"


def test_redirect_is_returned_without_second_connection(monkeypatch) -> None:
    adapter, connection, calls = transport_with(
        monkeypatch, Response(status=302, headers=[("Location", "http://localhost/private")])
    )
    assert adapter.get(request()).status == 302
    assert len(calls) == len(connection.requests) == 1
    assert connection.closed


@pytest.mark.parametrize(
    "error,code",
    [
        (TimeoutError("private"), "source_timeout"),
        (socket.gaierror("private"), "source_connection_error"),
        (ssl.SSLError("private"), "source_tls_error"),
        (http.client.BadStatusLine("private"), "source_protocol_error"),
    ],
)
def test_transport_open_failures_are_normalized_and_closed(monkeypatch, error, code) -> None:
    adapter, connection, _ = transport_with(monkeypatch, Response(), error=error)
    with pytest.raises(SourceFetchError) as caught:
        adapter.get(request())
    assert caught.value.code == code
    assert "private" not in str(caught.value)
    assert connection.closed


def test_bounded_prefix_is_preserved_but_not_called_complete(monkeypatch) -> None:
    adapter, connection, _ = transport_with(
        monkeypatch, Response(chunks=[b"123456789", b""]), maximum_body_bytes=8
    )
    result = adapter.get(request())
    assert result.body == b"12345678" and not result.body_complete
    assert result.capture_error == "response_too_large" and connection.closed
    collected, _, _ = run(result)
    assert collected.failure_code == "response_too_large"


def test_short_content_length_is_incomplete_not_success(monkeypatch) -> None:
    adapter, connection, _ = transport_with(
        monkeypatch, Response(chunks=[b"abc", b""], headers=[("Content-Length", "10")])
    )
    result = adapter.get(request())
    assert result.body == b"abc" and result.capture_error == "response_incomplete"
    assert not result.body_complete and connection.closed


def test_timeout_during_body_keeps_prefix_and_releases_connection(monkeypatch) -> None:
    adapter, connection, _ = transport_with(monkeypatch, Response(chunks=[b"abc", TimeoutError("private")]))
    result = adapter.get(request())
    assert result.body == b"abc" and result.capture_error == "source_timeout"
    assert not result.body_complete and connection.closed


def test_compressed_response_is_not_silently_treated_as_atom(monkeypatch) -> None:
    adapter, _, _ = transport_with(
        monkeypatch, Response(chunks=[b"compressed", b""], headers=[("Content-Encoding", "gzip")])
    )
    result, _, _ = run(adapter.get(request()))
    assert result.failure_code == "unsupported_content_encoding"


def test_disabled_transport_does_not_resolve_or_connect(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("network must stay off")

    monkeypatch.setattr(http.client, "HTTPSConnection", forbidden)
    with pytest.raises(SourceFetchError, match="source_fetch_disabled"):
        HttpClientArxivTransportAdapter().get(request())


@pytest.mark.parametrize(
    "options",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
        {"maximum_body_bytes": True},
        {"maximum_body_bytes": 8000001},
        {"user_agent": "test\r\nAuthorization: secret"},
    ],
)
def test_invalid_transport_configuration_is_rejected(options) -> None:
    with pytest.raises(SourceFetchError, match="invalid_transport_configuration"):
        HttpClientArxivTransportAdapter(**options)


@pytest.mark.parametrize("status", [429, 503])
def test_partial_error_body_still_honors_retry_after(status) -> None:
    response = SourceHttpResponse(
        status, b"bounded-prefix", (("retry-after", "120"),), NOW, "response_too_large"
    )
    result, gate, transport = run(response)
    assert result.failure_code == ("source_rate_limited" if status == 429 else "source_server_error")
    assert result.response is response and not result.response.body_complete
    assert result.retry_after_seconds == 120 and gate.delays == [120]
    assert transport.calls == 1


@pytest.mark.parametrize("invalid", [None, {}, [], True])
def test_wrong_request_type_has_stable_error_and_no_side_effects(invalid) -> None:
    gate, transport = Gate(), Transport(SourceHttpResponse(200, b"", (), NOW))
    result = ArxivSourceAdapter(transport, gate, enabled=True).fetch(invalid)
    assert result.failure_code == "invalid_source_request"
    assert result.request_fingerprint == "" and result.response is None
    assert transport.calls == gate.entries == 0
