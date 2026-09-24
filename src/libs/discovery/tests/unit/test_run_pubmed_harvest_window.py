from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from libs.discovery.application.commands.run_pubmed_harvest_window import (
    RunPubmedHarvestWindow,
)
from libs.discovery.dtos.pubmed_harvest_state import PendingPubmedBatch, PubmedHarvestState
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.kernel.dtos.object_ref import ObjectRef


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def state(
    *,
    status: str = "pending",
    page_start: int | None = None,
    pmids: tuple[str, ...] = (),
    batch_offset: int = 0,
    next_start: int = 0,
):
    return PubmedHarvestState(
        "unit:test",
        0 if next_start == 0 else 1,
        next_start,
        None if next_start == 0 else next_start,
        status,
        page_start,
        pmids,
        batch_offset,
    )


def response(body: bytes, *, status: int = 200):
    return SourceHttpResponse(status, body, (), NOW, None)


def object_ref(body: bytes):
    import hashlib

    digest = hashlib.sha256(body).hexdigest()
    return ObjectRef(
        "raw:" + digest,
        digest,
        f"objects/raw/{digest[:2]}/{digest}",
        "raw",
        "application/octet-stream",
        len(body),
        NOW.isoformat(),
        "source-response",
    )


def test_orchestrates_search_then_bibliography_outside_store_calls() -> None:
    source = Mock()
    plan = Mock()
    source.compile.return_value = plan
    search_request = Mock()
    bibliography_request = Mock()
    source.page.return_value = search_request
    source.bibliography_request.return_value = bibliography_request
    page = Mock()
    page.raw_body = b"search"
    page.response_sha256 = "a" * 64
    page.pmids = ("100",)
    batch = Mock()
    batch.raw_body = b"bibliography"
    batch.response_sha256 = "b" * 64
    source.parse_search.return_value = page
    source.parse_bibliography.return_value = batch

    transport = Mock()
    transport.get.side_effect = [response(b"search"), response(b"bibliography")]
    objects = Mock(side_effect=lambda body, *_: object_ref(body))
    store = Mock()
    store.ensure.return_value = state()
    store.read.side_effect = [
        state(),
        state(page_start=0, pmids=("100",)),
        state(status="succeeded", next_start=1),
    ]
    store.save_search.return_value = state(page_start=0, pmids=("100",))
    pending = PendingPubmedBatch("unit:test", 0, 0, ("100",))
    store.next_batch.return_value = pending
    store.save_bibliography.return_value = state(status="succeeded", next_start=1)

    result = RunPubmedHarvestWindow(source, transport, objects, store)(
        Mock(),
        started_at=NOW,
        max_batches=5,
    )

    assert result.stop_reason == "complete"
    assert result.search_fetches == 1
    assert result.bibliography_fetches == 1
    assert transport.get.call_count == 2
    assert objects.call_count == 2
    source.bibliography_request.assert_called_once_with(("100",))


def test_rate_limit_stops_before_any_store_checkpoint_write() -> None:
    source = Mock()
    source.compile.return_value = Mock()
    source.page.return_value = Mock()
    transport = Mock()
    transport.get.return_value = response(b"rate", status=429)
    store = Mock()
    store.ensure.return_value = state()
    store.read.return_value = state()
    objects = Mock()

    with pytest.raises(SourceFetchError, match="source_rate_limited"):
        RunPubmedHarvestWindow(source, transport, objects, store)(
            Mock(),
            started_at=NOW,
        )

    store.save_search.assert_not_called()
    store.save_bibliography.assert_not_called()
    objects.assert_not_called()
