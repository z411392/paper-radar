from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from libs.discovery.adapters.driven.http_client_ncbi_transport_adapter import (
    HttpClientNcbiTransportAdapter,
)
from libs.discovery.adapters.driven.posix_ncbi_rate_limit_adapter import (
    PosixNcbiRateLimitAdapter,
)
from libs.discovery.adapters.driven.rate_limited_source_http_transport_adapter import (
    RateLimitedSourceHttpTransportAdapter,
)
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def request(url: str, source: str = "pubmed") -> SourcePageRequest:
    return SourcePageRequest(
        source,
        "a" * 64,
        "b" * 64,
        "GET",
        url,
        0,
        1,
        1,
    )


def test_ncbi_transport_validator_allows_only_fixed_hosts_and_paths() -> None:
    host, target = HttpClientNcbiTransportAdapter._target(
        request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
            "db=pubmed&term=badminton"
        )
    )
    assert host == "eutils.ncbi.nlm.nih.gov"
    assert target.startswith("/entrez/eutils/esearch.fcgi?")

    for value in (
        "https://example.org/entrez/eutils/esearch.fcgi?db=pubmed",
        "https://eutils.ncbi.nlm.nih.gov/other?db=pubmed",
        "http://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed",
    ):
        with pytest.raises(SourceFetchError, match="invalid_page_request"):
            HttpClientNcbiTransportAdapter._target(request(value))


def test_disabled_ncbi_transport_never_opens_network() -> None:
    adapter = HttpClientNcbiTransportAdapter(enabled=False)
    with pytest.raises(SourceFetchError, match="source_fetch_disabled"):
        adapter.get(
            request(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
                "db=pubmed&term=badminton"
            )
        )


def test_rate_limited_wrapper_defers_retry_after() -> None:
    response = SourceHttpResponse(
        429,
        b"",
        (("retry-after", "12"),),
        NOW,
        None,
    )
    transport = Mock()
    transport.get.return_value = response
    lease = Mock()
    manager = Mock()
    manager.__enter__ = Mock(return_value=lease)
    manager.__exit__ = Mock(return_value=False)
    gate = Mock()
    gate.slot.return_value = manager

    actual = RateLimitedSourceHttpTransportAdapter(transport, gate).get(
        request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
            "db=pubmed&term=badminton"
        )
    )

    assert actual is response
    lease.defer.assert_called_once_with(12.0)


def test_ncbi_rate_file_uses_provider_specific_state(tmp_path: Path) -> None:
    parent = tmp_path / "rates"
    parent.mkdir()
    path = parent / "ncbi.json"
    clock = Mock(return_value=100.0)
    gate = PosixNcbiRateLimitAdapter(
        path,
        api_key_present=False,
        clock=clock,
    )

    with gate.slot():
        pass

    raw = path.read_text(encoding="ascii")
    assert '"provider":"ncbi"' in raw
    assert '"not_before":100.34' in raw
