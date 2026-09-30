import gzip
import http.client
import zlib
from io import BytesIO
from unittest.mock import Mock, patch

import pytest

from libs.discovery.adapters.driven.http_client_ncbi_transport_adapter import (
    HttpClientNcbiTransportAdapter,
)
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


def request() -> SourcePageRequest:
    return SourcePageRequest(
        "pubmed", "a" * 64, "b" * 64, "GET",
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=1",
        0, 1, 1,
    )


def retrieve(wire: bytes, *, encoding="identity", length=None, limit=4096, extra=()):
    headers = [("Content-Type", "application/xml"), ("Content-Encoding", encoding)]
    if length is not None:
        headers.append(("Content-Length", str(length)))
    headers.extend(extra)
    raw = b"HTTP/1.1 200 OK\r\n" + b"".join(
        f"{key}: {value}\r\n".encode("ascii") for key, value in headers
    ) + b"\r\n" + wire
    socket = Mock()
    socket.makefile.return_value = BytesIO(raw)
    response = http.client.HTTPResponse(socket)
    response.begin()
    connection = Mock()
    connection.getresponse.return_value = response
    with patch("http.client.HTTPSConnection", return_value=connection):
        result = HttpClientNcbiTransportAdapter(
            enabled=True, maximum_body_bytes=limit,
        ).get(request())
    connection.close.assert_called_once()
    assert response.isclosed()
    return result


@pytest.mark.parametrize("encoding", ["identity", "gzip", "deflate"])
def test_complete_entity_is_returned_and_wire_headers_are_not_mislabelled(encoding):
    body = b"<PubmedArticleSet/>"
    wire = {"identity": body, "gzip": gzip.compress(body), "deflate": zlib.compress(body)}[encoding]
    result = retrieve(wire, encoding=encoding, length=len(wire))
    assert result.body == body
    assert result.capture_error is None
    # Returned body is transfer-decoded. Do not persist wire encoding/length as its headers.
    assert not {"content-encoding", "content-length"} & {key for key, _ in result.headers}


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_truncated_compressed_stream_cannot_be_successful(encoding):
    body = b"<PubmedArticleSet/>"
    wire = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)
    result = retrieve(wire[:-4], encoding=encoding)
    assert result.capture_error == "response_incomplete"
    assert result.body_complete is False


def test_short_content_length_is_an_incomplete_response():
    result = retrieve(b"<x/>", length=20)
    assert result.capture_error == "response_incomplete"


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_trailing_compressed_bytes_are_not_silently_discarded(encoding):
    wire = gzip.compress(b"<x/>") if encoding == "gzip" else zlib.compress(b"<x/>")
    result = retrieve(wire + b"unaccounted", encoding=encoding)
    assert result.capture_error == "invalid_content_encoding"


@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_decoded_body_limit_blocks_high_expansion_responses(encoding):
    wire = gzip.compress(b"x" * 100000) if encoding == "gzip" else zlib.compress(b"x" * 100000)
    result = retrieve(wire, encoding=encoding, limit=256)
    assert result.capture_error == "response_too_large"
    assert len(result.body) <= 256


@pytest.mark.parametrize("length", ["-1", "garbage", "1,1"])
def test_invalid_content_length_is_not_accepted(length):
    result = retrieve(b"<x/>", length=length)
    assert result.capture_error == "invalid_response_headers"


def test_duplicate_content_length_is_not_accepted():
    result = retrieve(b"<x/>", length=4, extra=(("Content-Length", "4"),))
    assert result.capture_error == "invalid_response_headers"


def test_unsupported_encoding_is_not_a_successful_capture():
    result = retrieve(b"compressed", encoding="br")
    assert result.capture_error == "unsupported_content_encoding"


def test_default_transport_does_not_open_a_connection():
    with patch("http.client.HTTPSConnection") as connection:
        with pytest.raises(SourceFetchError, match="source_fetch_disabled"):
            HttpClientNcbiTransportAdapter().get(request())
    connection.assert_not_called()
