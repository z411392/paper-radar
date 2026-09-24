"""Real stdlib HTTP framing over memory sockets; no DNS, TLS or network."""
import gzip
import http.client
import io
import zlib
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.http_client_crossref_transport_adapter import HttpClientCrossrefTransportAdapter
from libs.discovery.domain.services.crossref_capture_rules import CrossrefCaptureRules
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
EMAIL = "paper@example.org"
BODY = b'{"status":"ok","message-type":"work-list","message":{"items":[]}}'


def plan_request():
    source = CrossrefSourceAdapter()
    plan = source.compile(CrossrefWindowInput(
        "binding:statistics", "statistics", NOW.replace(day=23), NOW, EMAIL, "v1", 2,
    ))
    return plan, source.page(plan)


class MemorySocket:
    def __init__(self, raw):
        self.raw = raw
    def makefile(self, mode):
        return io.BytesIO(self.raw)


class MemoryConnection:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []
        self.closed = False
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
    def getresponse(self):
        response = http.client.HTTPResponse(MemorySocket(self.raw))
        response.begin()
        return response
    def close(self):
        self.closed = True


def response(body=BODY, headers=(), status=200, length=True):
    fields = list(headers)
    if length:
        fields.append(("Content-Length", str(len(body))))
    return (f"HTTP/1.1 {status} Test\r\n" + "".join(f"{k}: {v}\r\n" for k,v in fields)
            + "\r\n").encode("ascii") + body


def capture(raw, **kwargs):
    conn = MemoryConnection(raw)
    p, q = plan_request()
    with patch("http.client.HTTPSConnection", return_value=conn) as factory:
        result = HttpClientCrossrefTransportAdapter(enabled=True, contact_email=EMAIL, **kwargs).get(p, q)
    assert conn.closed
    assert len(conn.calls) == 1
    assert factory.call_args.args == ("api.crossref.org",)
    return result, conn


@pytest.mark.parametrize("encoding", ["identity", "gzip", "deflate"])
def test_complete_content_coded_bytes_are_not_lost(encoding):
    encoded = BODY if encoding == "identity" else gzip.compress(BODY) if encoding == "gzip" else zlib.compress(BODY)
    result, conn = capture(response(encoded, (("Content-Encoding", encoding),)))
    assert result.body == encoded
    assert result.complete is True
    assert result.capture_error is None
    assert dict(result.headers)["content-length"] == str(len(encoded))
    assert CrossrefCaptureRules.entity(result) == BODY
    headers = conn.calls[0][1]["headers"]
    assert headers["Accept-Encoding"] == "gzip, deflate"
    assert EMAIL in headers["User-Agent"]


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 403, 404, 429, 500, 503])
def test_http_errors_are_captured_without_following_or_retrying(status):
    result, _ = capture(response(b"failure", (("Location", "https://publisher.example/fulltext"),), status))
    assert result.status == status
    assert result.complete
    assert result.body == b"failure"
    assert dict(result.headers)["location"] == "https://publisher.example/fulltext"
    with pytest.raises(CrossrefCaptureError, match="crossref_capture_not_parseable"):
        CrossrefCaptureRules.entity(result)


def test_default_disabled_does_not_construct_connection():
    p, q = plan_request()
    with patch("http.client.HTTPSConnection") as factory:
        with pytest.raises(CrossrefCaptureError, match="crossref_transport_disabled"):
            HttpClientCrossrefTransportAdapter(contact_email=EMAIL).get(p, q)
        factory.assert_not_called()


@pytest.mark.parametrize("change", [
    {"url": "https://evil.example/works"}, {"request_fingerprint": "0"*64},
    {"cursor": "different"}, {"parameters_fingerprint": "0"*64},
])
def test_tampered_request_never_reaches_transport(change):
    p, q = plan_request()
    with patch("http.client.HTTPSConnection") as factory:
        with pytest.raises(CrossrefCaptureError):
            HttpClientCrossrefTransportAdapter(enabled=True, contact_email=EMAIL).get(p, replace(q, **change))
        factory.assert_not_called()


def test_contact_cannot_silently_switch_budget():
    p, q = plan_request()
    with patch("http.client.HTTPSConnection") as factory:
        with pytest.raises(CrossrefCaptureError, match="crossref_contact_mismatch"):
            HttpClientCrossrefTransportAdapter(enabled=True, contact_email="other@example.org").get(p, q)
        factory.assert_not_called()


def test_short_content_length_body_is_incomplete():
    result, _ = capture(response(b"short", (("Content-Length", "30"),), length=False))
    assert not result.complete
    assert result.capture_error == "response_incomplete"
    assert result.body == b"short"
    with pytest.raises(CrossrefCaptureError):
        CrossrefCaptureRules.entity(result)


@pytest.mark.parametrize("headers", [
    (("Content-Length", "-1"),), (("Content-Length", "no"),),
    (("Content-Length", "3"), ("Content-Length", "4")),
    (("Content-Length", "3"), ("Transfer-Encoding", "chunked")),
    (("Transfer-Encoding", "gzip"),),
])
def test_ambiguous_or_invalid_framing_cannot_be_complete(headers):
    result, _ = capture(response(b"abc", headers, length=False))
    assert not result.complete
    assert result.capture_error == "invalid_response_framing"


def test_chunked_body_is_captured_after_transfer_decoding():
    encoded = f"{len(BODY):x}\r\n".encode() + BODY + b"\r\n0\r\n\r\n"
    result, _ = capture(response(encoded, (("Transfer-Encoding", "chunked"),), length=False))
    assert result.complete
    assert result.body == BODY
    assert CrossrefCaptureRules.entity(result) == BODY


def test_truncated_chunked_message_is_incomplete():
    encoded = b"5\r\nhello\r\n5\r\nxy"
    result, _ = capture(response(encoded, (("Transfer-Encoding", "chunked"),), length=False))
    assert not result.complete
    assert result.capture_error == "response_incomplete"
    assert result.body.startswith(b"hello")


def test_close_delimited_body_is_explicitly_supported():
    result, _ = capture(response(BODY, length=False))
    assert result.complete
    assert CrossrefCaptureRules.entity(result) == BODY


def test_wire_limit_retains_prefix_not_a_success_body():
    result, _ = capture(response(b"a"*100), maximum_body_bytes=40)
    assert result.body == b"a"*40
    assert not result.complete
    assert result.capture_error == "response_too_large"


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
@pytest.mark.parametrize("fault", ["truncated", "trailing", "corrupt", "concatenated"])
def test_broken_or_unsupported_compression_is_saved_but_not_parsed(encoding, fault):
    coded = gzip.compress(BODY) if encoding == "gzip" else zlib.compress(BODY)
    coded = coded[:-2] if fault == "truncated" else coded+b"junk" if fault == "trailing" else b"bad" if fault == "corrupt" else coded+coded
    result, _ = capture(response(coded, (("Content-Encoding", encoding),)))
    assert result.complete and result.body == coded
    with pytest.raises(CrossrefCaptureError, match="crossref_content_decoding_failed"):
        CrossrefCaptureRules.entity(result)


@pytest.mark.parametrize("encoding", ["br", "gzip, deflate", "zstd"])
def test_unsupported_content_encoding_does_not_discard_raw(encoding):
    result, _ = capture(response(BODY, (("Content-Encoding", encoding),)))
    assert result.body == BODY
    with pytest.raises(CrossrefCaptureError, match="crossref_content_encoding_unsupported"):
        CrossrefCaptureRules.entity(result)


def test_decompression_is_bounded():
    coded = gzip.compress(b"a"*100_000)
    result, _ = capture(response(coded, (("Content-Encoding", "gzip"),)))
    with pytest.raises(CrossrefCaptureError, match="crossref_entity_too_large"):
        CrossrefCaptureRules.entity(result, maximum_bytes=100)


def test_no_response_keeps_unknown_status_not_fake_200():
    p, q = plan_request()
    with patch("http.client.HTTPSConnection", side_effect=TimeoutError):
        result = HttpClientCrossrefTransportAdapter(enabled=True, contact_email=EMAIL).get(p, q)
    assert result.status is None and result.body == b""
    assert result.complete is False
    assert result.capture_error == "source_timeout"


def test_sensitive_response_headers_not_retained():
    result, _ = capture(response(headers=(("Set-Cookie", "private=value"), ("X-Rate-Limit-Limit", "3"))))
    assert "set-cookie" not in dict(result.headers)
    assert dict(result.headers)["x-rate-limit-limit"] == "3"


@pytest.mark.parametrize("kwargs", [
    {"enabled":1}, {"timeout_seconds":0}, {"timeout_seconds":float("nan")},
    {"maximum_body_bytes":True}, {"maximum_body_bytes":9_000_000},
    {"contact_email":"bad\r\nheader"},
])
def test_invalid_transport_config(kwargs):
    values = {"contact_email": EMAIL, **kwargs}
    with pytest.raises(CrossrefCaptureError):
        HttpClientCrossrefTransportAdapter(**values)
