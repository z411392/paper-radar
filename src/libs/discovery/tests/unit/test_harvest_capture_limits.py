import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from libs.discovery.application.commands.record_harvest_capture import RecordHarvestCapture
from libs.discovery.domain.services.prepare_harvest_capture import PrepareHarvestCapture
from libs.discovery.dtos.harvest_attempt import HarvestAttempt
from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.exceptions.harvest_error import HarvestError

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)
REQUEST = SourcePageRequest("arxiv", "a" * 64, "b" * 64, "GET", "fixture", 0, 2, 30000)
ATTEMPT = HarvestAttempt("attempt-a", "unit:a", 1, REQUEST, NOW.isoformat(timespec="microseconds"), "running")


@pytest.mark.parametrize("delay", [10**1000, -(10**1000)], ids=["positive-overflow", "negative-overflow"])
def test_overflowing_retry_delay_is_a_stable_error_before_writes(delay: int) -> None:
    store = Mock()
    store.read.return_value = ATTEMPT
    publisher = Mock()
    result = SourceFetchResult(REQUEST.request_fingerprint, None, None, "http_timeout", True, delay)
    with pytest.raises(HarvestError, match="^invalid_capture_retry$"):
        RecordHarvestCapture(store, publisher)("attempt-a", result, NOW)
    publisher.assert_not_called()
    store.record.assert_not_called()


@pytest.mark.parametrize("delay", [0, 3.0, None])
def test_valid_retry_delays_are_preserved(delay: float | None) -> None:
    result = SourceFetchResult(REQUEST.request_fingerprint, None, None, "http_timeout", True, delay)
    prepared = PrepareHarvestCapture()(ATTEMPT, result, NOW)
    assert json.loads(prepared.metadata_json)["retry_after_seconds"] == delay
    assert prepared.body is None
