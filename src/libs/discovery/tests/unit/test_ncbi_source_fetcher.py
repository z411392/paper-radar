from contextlib import contextmanager
from datetime import datetime, timezone

from libs.discovery.adapters.driven.ncbi_source_fetcher_adapter import (
    NcbiSourceFetcherAdapter,
)
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_page_request import SourcePageRequest


class Gate:
    def __init__(self):
        self.deferrals = []

    @contextmanager
    def slot(self):
        outer = self

        class Lease:
            def defer(self, seconds):
                outer.deferrals.append(seconds)

        yield Lease()


class Transport:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, request):
        self.calls += 1
        return self.response


def request(
    url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed",
):
    return SourcePageRequest(
        "pubmed",
        "a" * 64,
        "b" * 64,
        "GET",
        url,
        0,
        10,
        100,
    )


def response(status=200, headers=()):
    return SourceHttpResponse(
        status,
        b"{}",
        tuple(headers),
        datetime(2026, 9, 24, tzinfo=timezone.utc),
        None,
    )


def test_ncbi_fetcher_is_disabled_by_default() -> None:
    transport = Transport(response())
    result = NcbiSourceFetcherAdapter(transport, Gate()).fetch(request())

    assert result.failure_code == "source_fetch_disabled"
    assert transport.calls == 0


def test_ncbi_fetcher_rejects_wrong_host_before_transport() -> None:
    transport = Transport(response())
    fetcher = NcbiSourceFetcherAdapter(transport, Gate(), enabled=True)

    result = fetcher.fetch(
        request("https://example.com/entrez/eutils/esearch.fcgi")
    )

    assert result.failure_code == "invalid_source_request"
    assert transport.calls == 0


def test_ncbi_429_defers_shared_provider_gate() -> None:
    gate = Gate()
    fetcher = NcbiSourceFetcherAdapter(
        Transport(response(429, (("retry-after", "2"),))),
        gate,
        enabled=True,
    )

    result = fetcher.fetch(request())

    assert result.failure_code == "source_rate_limited"
    assert result.retryable is True
    assert result.retry_after_seconds == 2.0
    assert gate.deferrals == [2.0]
