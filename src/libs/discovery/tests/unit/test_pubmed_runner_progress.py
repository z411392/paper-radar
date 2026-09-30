from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from libs.discovery.application.commands.run_pubmed_harvest_window import RunPubmedHarvestWindow
from libs.discovery.dtos.pubmed_harvest_state import PendingPubmedBatch, PubmedHarvestState
from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_fetch_error import SourceFetchError


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
PAGE = PubmedHarvestState("unit:test", 0, 0, 1, "pending", 0, ("100",), 0)
DONE = PubmedHarvestState("unit:test", 1, 1, 1, "succeeded", None, (), 0)


def setup_runner():
    source, transport, objects, store = Mock(), Mock(), Mock(), Mock()
    store.ensure.return_value = PAGE
    return RunPubmedHarvestWindow(source, transport, objects, store), source, transport, objects, store


def test_nonterminal_without_next_batch_or_changed_checkpoint_fails_instead_of_spinning():
    command, _, transport, _, store = setup_runner()
    # Finite fuse turns the old unbounded loop into a reproducible failure, never a hanging test.
    store.read.side_effect = [PAGE, PAGE, RuntimeError("unbounded-loop-fuse")]
    store.next_batch.return_value = None
    with pytest.raises(HarvestError, match="pubmed_harvest_no_progress"):
        command(Mock(), started_at=NOW, max_batches=1)
    assert store.read.call_count <= 2
    transport.get.assert_not_called()


def test_other_worker_completing_between_read_and_next_batch_is_not_an_error():
    command, _, transport, _, store = setup_runner()
    store.read.side_effect = [PAGE, DONE, DONE]
    store.next_batch.return_value = None
    result = command(Mock(), started_at=NOW, max_batches=1)
    assert result.stop_reason == "complete"
    assert result.progress == DONE
    assert result.search_fetches == result.bibliography_fetches == 0
    transport.get.assert_not_called()


def test_continuous_concurrent_progress_still_has_a_finite_cycle_budget():
    command, _, transport, _, store = setup_runner()
    calls = 0

    def advancing(_):
        nonlocal calls
        calls += 1
        if calls > 20:
            raise RuntimeError("unbounded-loop-fuse")
        return replace(PAGE, checkpoint_version=calls, next_start=calls, page_start=calls)

    store.read.side_effect = advancing
    store.next_batch.return_value = None
    result = command(Mock(), started_at=NOW, max_batches=1)
    assert result.stop_reason == "page_budget"
    assert calls <= 8
    transport.get.assert_not_called()


def test_resuming_saved_search_fetches_only_pending_bibliography():
    command, source, transport, objects, store = setup_runner()
    store.read.side_effect = [PAGE, DONE]
    store.next_batch.return_value = PendingPubmedBatch("unit:test", 0, 0, ("100",))
    store.save_bibliography.return_value = DONE
    transport.get.return_value = SourceHttpResponse(200, b"xml", (), NOW)
    objects.return_value.object_id = "raw:fixture"
    result = command(Mock(), started_at=NOW, max_batches=1)
    assert result.stop_reason == "complete"
    assert result.bibliography_fetches == 1
    assert result.search_fetches == 0
    source.page.assert_not_called()
    source.parse_search.assert_not_called()
    source.bibliography_request.assert_called_once_with(("100",))
    store.save_search.assert_not_called()
    store.save_bibliography.assert_called_once()


@pytest.mark.parametrize("code", ["response_incomplete", "source_timeout"])
def test_failed_efetch_never_writes_observations_or_advances_checkpoint(code):
    command, source, transport, objects, store = setup_runner()
    store.read.return_value = PAGE
    store.next_batch.return_value = PendingPubmedBatch("unit:test", 0, 0, ("100",))
    transport.get.return_value = SourceHttpResponse(200, b"prefix", (), NOW, code)
    with pytest.raises(SourceFetchError, match=code):
        command(Mock(), started_at=NOW)
    source.parse_bibliography.assert_not_called()
    objects.assert_not_called()
    store.save_bibliography.assert_not_called()
    store.save_search.assert_not_called()


def test_already_completed_window_never_fetches_again():
    command, source, transport, objects, store = setup_runner()
    store.read.return_value = DONE
    result = command(Mock(), started_at=NOW)
    assert result.stop_reason == "complete"
    transport.get.assert_not_called()
    objects.assert_not_called()
    source.page.assert_not_called()


def test_invalid_budget_is_rejected_before_any_side_effect():
    command, source, transport, objects, store = setup_runner()
    with pytest.raises(HarvestError, match="invalid_pubmed_batch_limit"):
        command(Mock(), started_at=NOW, max_batches=0)
    source.compile.assert_not_called()
    store.ensure.assert_not_called()
    transport.get.assert_not_called()
    objects.assert_not_called()
