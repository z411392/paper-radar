"""Multi-page continuation and plan-envelope guards for the PubMed store."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.tests.integration.test_pubmed_checkpoint_integrity import (
    NOW,
    bibliography,
    commit_next,
    page,
    plan,
    prepare,
    publish,
    sql,
)
from libs.discovery.tests.integration.test_pubmed_checkpoint_integrity import workspace as workspace


def test_multiple_pages_and_old_replay_preserve_monotonic_cursor(workspace):
    path, store = workspace
    definition, first_search = prepare(path, store, total=3)
    partial, old, batch, object_id = commit_next(path, store, definition)
    assert (partial.state, partial.next_start, partial.checkpoint_version) == ("partial", 2, 1)
    assert store.save_search(definition, first_search, publish(path, first_search.raw_body), NOW) == partial
    second_search = page(definition, ("300",), start=2, total=3)
    store.save_search(definition, second_search, publish(path, second_search.raw_body), NOW)
    final, _, _, _ = commit_next(path, store, definition)
    assert (final.state, final.next_start, final.checkpoint_version) == ("succeeded", 3, 2)
    assert store.save_bibliography(definition, old, batch, object_id, NOW) == final
    assert sql(path, "SELECT COUNT(*) FROM source_observations") == [(3,)]


def test_later_page_total_drift_does_not_destroy_completed_prefix(workspace):
    path, store = workspace
    definition, _ = prepare(path, store, total=4)
    before, _, _, _ = commit_next(path, store, definition)
    changed = page(definition, ("300", "400"), start=2, total=5)
    with pytest.raises(HarvestError, match="pubmed_result_set_changed"):
        store.save_search(definition, changed, publish(path, changed.raw_body), NOW)
    assert store.read(definition) == before
    assert sql(path, "SELECT COUNT(*) FROM pubmed_harvest_pages") == [(1,)]


def test_duplicate_across_pages_is_rejected_without_extending_cursor(workspace):
    path, store = workspace
    definition, _ = prepare(path, store, total=4)
    before, _, _, _ = commit_next(path, store, definition)
    duplicate = page(definition, ("200", "300"), start=2, total=4)
    with pytest.raises(HarvestError):
        store.save_search(definition, duplicate, publish(path, duplicate.raw_body), NOW)
    assert store.read(definition) == before


@pytest.mark.parametrize("change", [
    {"page_size": 1}, {"search_query": "changed"}, {"compiler_version": "wrong"},
    {"capability_version": "wrong"},
])
def test_plan_outer_fields_cannot_disagree_with_hashed_envelope(workspace, change):
    path, store = workspace
    with pytest.raises(HarvestError, match="invalid_harvest_definition"):
        store.ensure(replace(plan(), **change), NOW)
    assert sql(path, "SELECT COUNT(*) FROM source_bindings") == [(0,)]


def test_concurrent_conflicting_bibliographies_cannot_overwrite_winner(workspace):
    path, store = workspace
    definition, _ = prepare(path, store)
    pending = store.next_batch(definition, maximum_batch_size=2)
    variants = [bibliography(pending), bibliography(pending, suffix=" revised")]
    objects = [publish(path, item.raw_body) for item in variants]
    barrier = Barrier(2)

    def worker(index):
        barrier.wait(timeout=3)
        try:
            return store.save_bibliography(definition, pending, variants[index], objects[index], NOW).state
        except HarvestError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, index) for index in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(results) == ["pubmed_batch_conflict", "succeeded"]
    assert sql(path, "SELECT COUNT(*) FROM source_observations") == [(2,)]
    assert sql(path, "SELECT COUNT(*) FROM pubmed_bibliography_batches") == [(1,)]
    assert store.read(definition).checkpoint_version == 1


@pytest.mark.parametrize("change", [
    {"parser_version": "wrong"}, {"request_fingerprint": "wrong"},
    {"raw_body": "not bytes"}, {"pmids": ["100", "200"]},
])
def test_invalid_typed_search_receipt_is_rejected_before_write(workspace, change):
    path, store = workspace
    definition = plan()
    store.ensure(definition, NOW)
    receipt = page(definition)
    object_id = publish(path, receipt.raw_body)
    with pytest.raises(HarvestError):
        store.save_search(definition, replace(receipt, **change), object_id, NOW)
    assert sql(path, "SELECT COUNT(*) FROM pubmed_harvest_pages") == [(0,)]
