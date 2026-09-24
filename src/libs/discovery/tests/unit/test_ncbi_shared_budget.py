import json

import pytest

from libs.discovery.adapters.driven.posix_ncbi_rate_limit_adapter import PosixNcbiRateLimitAdapter
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


@pytest.mark.parametrize("has_key", [False, True])
def test_shared_ncbi_gate_keeps_pmc_safe_budget_with_or_without_eutils_key(tmp_path, has_key):
    path = tmp_path / "ncbi.json"
    gate = PosixNcbiRateLimitAdapter(path, api_key_present=has_key, clock=lambda: 100.0)
    with gate.slot():
        pass
    assert json.loads(path.read_text())["not_before"] == 100.34
    with pytest.raises(SourceFetchError, match="provider_deferred"):
        with PosixNcbiRateLimitAdapter(path, api_key_present=has_key, clock=lambda: 100.2).slot():
            pytest.fail("a shared PMC budget was raised by an EUtils key")


def test_explicit_interval_cannot_exceed_shared_provider_throughput(tmp_path):
    with pytest.raises(SourceFetchError, match="invalid_transport_configuration"):
        PosixNcbiRateLimitAdapter(tmp_path / "ncbi.json", minimum_interval_seconds=0.11)
