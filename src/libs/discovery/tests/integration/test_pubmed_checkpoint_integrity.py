"""SQLite boundary tests: canonical schema, typed provider fixtures, no network."""

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier

import pytest

from libs.discovery.adapters.driven.sqlite_pubmed_harvest_store_adapter import (
    SqlitePubmedHarvestStoreAdapter,
)
from libs.discovery.dtos.compiled_source_query import CompiledSourceQuery
from libs.discovery.dtos.pubmed_bibliography_batch import PubmedBibliographyBatch
from libs.discovery.dtos.pubmed_bibliography_record import PubmedBibliographyRecord
from libs.discovery.dtos.pubmed_search_page import PubmedSearchPage
from libs.discovery.dtos.source_page_observation import SourcePageObservation
from libs.discovery.exceptions.harvest_error import HarvestError

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
PARSER = "pubmed-eutils-parser-v1"


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def plan(profile: str = "personal") -> CompiledSourceQuery:
    search_query = '"badminton"[Title/Abstract]'
    provenance = canonical({
        "compiler_version": "pubmed-eutils-v1",
        "capability_version": "ncbi-eutils-20260924-v1",
        "policy": "title-abstract-crdt-v1",
        "input": {
            "source_id": "pubmed",
            "profile_id": profile,
            "profile_revision": 1,
            "profile_fingerprint": "a" * 64,
            "domain": {"domain_id": "badminton", "revision": 1},
            "window_start": "2026-09-23T00:00:00+00:00",
            "window_end": "2026-09-24T00:00:00+00:00",
            "provider_mindate": "2026/09/23",
            "provider_maxdate": "2026/09/23",
            "search_query": search_query,
            "page_size": 2,
            "deferred_filters": [],
            "warnings": [],
        },
    })
    return CompiledSourceQuery(
        "pubmed", "pubmed-eutils-v1", "ncbi-eutils-20260924-v1",
        hashlib.sha256(provenance.encode()).hexdigest(), provenance, search_query,
        2, 10000, (), (),
    )


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, isolation_level=None, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@pytest.fixture
def workspace(tmp_path: Path):
    path = tmp_path / "pubmed.sqlite3"
    db = connect(path)
    root = Path(__file__).resolve().parents[5]
    for name in (
        "0001-object-registry.sql", "0002-watch-profiles.sql",
        "0004-discovery.sql", "0010-pubmed-harvest.sql",
    ):
        db.executescript((root / "migrations" / name).read_text(encoding="utf-8"))
    with db:
        for profile in ("personal", "other"):
            db.execute(
                "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
                (profile, "reader:local", profile, "active", None, NOW.isoformat()),
            )
            db.execute(
                "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
                (profile, 1, "scope", '{"sources":["pubmed"]}', "a" * 64, NOW.isoformat()),
            )
            db.execute("UPDATE watch_profiles SET published_revision=1 WHERE id=?", (profile,))
    db.close()
    return path, SqlitePubmedHarvestStoreAdapter(lambda: connect(path))


def sql(path: Path, statement: str, args: tuple = ()) -> list[tuple]:
    db = connect(path)
    try:
        return [tuple(row) for row in db.execute(statement, args).fetchall()]
    finally:
        db.close()


def publish(path: Path, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    object_id = "raw:" + digest
    sql(path, "INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)", (
        object_id, digest, f"objects/raw/{digest[:2]}/{digest}", "raw",
        "application/octet-stream", len(body), "available", NOW.isoformat(), "test",
    ))
    return object_id


def page(definition, pmids=("100", "200"), *, start=0, total=None):
    total = len(pmids) if total is None else total
    body = canonical({"esearchresult": {
        "count": str(total), "retstart": str(start), "idlist": list(pmids),
    }}).encode()
    return PubmedSearchPage(
        SourcePageObservation("pubmed", definition.query_fingerprint, start, total, tuple(pmids)),
        tuple(pmids), body, hashlib.sha256(body).hexdigest(), "b" * 64, PARSER,
    )


def prepare(path, store, definition=None, *, pmids=("100", "200"), total=None):
    definition = definition or plan()
    store.ensure(definition, NOW)
    result = page(definition, pmids, total=total)
    store.save_search(definition, result, publish(path, result.raw_body), NOW)
    return definition, result


def bibliography(pending, *, suffix=""):
    records = tuple(PubmedBibliographyRecord(
        pmid, f"Paper {pmid}{suffix}", "Abstract", (), None, None, None, None, None, (), (),
    ) for pmid in pending.pmids)
    articles = "".join(
        f"<PubmedArticle><MedlineCitation><PMID>{r.pmid}</PMID><Article>"
        f"<ArticleTitle>{r.title}</ArticleTitle></Article></MedlineCitation></PubmedArticle>"
        for r in records
    )
    body = f"<PubmedArticleSet>{articles}</PubmedArticleSet>".encode()
    return PubmedBibliographyBatch(records, body, hashlib.sha256(body).hexdigest(), "c" * 64, PARSER)


def commit_next(path, store, definition, size=2):
    pending = store.next_batch(definition, maximum_batch_size=size)
    assert pending is not None
    batch = bibliography(pending)
    object_id = publish(path, batch.raw_body)
    result = store.save_bibliography(definition, pending, batch, object_id, NOW)
    return result, pending, batch, object_id


def completed(workspace):
    path, store = workspace
    definition, _ = prepare(path, store)
    result, pending, batch, object_id = commit_next(path, store, definition)
    assert result.state == "succeeded"
    return path, store, definition, pending, batch, object_id


def test_partial_commit_survives_reopen_and_points_to_bibliography(workspace):
    path, store = workspace
    definition, search = prepare(path, store)
    partial, first, first_batch, first_object = commit_next(path, store, definition, 1)
    assert (partial.next_start, partial.checkpoint_version, partial.next_batch_offset) == (0, 0, 1)
    reopened = SqlitePubmedHarvestStoreAdapter(lambda: connect(path))
    assert reopened.next_batch(definition, maximum_batch_size=1).pmids == ("200",)
    final, _, _, _ = commit_next(path, reopened, definition, 1)
    assert (final.state, final.next_start, final.checkpoint_version) == ("succeeded", 2, 1)
    assert reopened.save_bibliography(definition, first, first_batch, first_object, NOW) == final
    payloads = sql(path, "SELECT native_id,payload_object_id FROM source_observations ORDER BY native_id")
    assert len(payloads) == 2
    assert all(value[1] != "raw:" + search.response_sha256 for value in payloads)


@pytest.mark.parametrize("failure_table", [
    "source_observations", "pubmed_bibliography_batches", "pubmed_harvest_pages", "harvest_units",
])
def test_transaction_failure_rolls_back_entire_batch(workspace, failure_table):
    path, store = workspace
    definition, _ = prepare(path, store)
    pending = store.next_batch(definition, maximum_batch_size=2)
    batch = bibliography(pending)
    object_id = publish(path, batch.raw_body)
    action = "UPDATE" if failure_table in {"pubmed_harvest_pages", "harvest_units"} else "INSERT"
    condition = "WHEN NEW.native_id='200' " if failure_table == "source_observations" else ""
    sql(path, f"CREATE TRIGGER injected_failure BEFORE {action} ON {failure_table} "
        f"{condition}BEGIN SELECT RAISE(ABORT,'test crash'); END")
    with pytest.raises(HarvestError):
        store.save_bibliography(definition, pending, batch, object_id, NOW)
    assert sql(path, "SELECT COUNT(*) FROM source_observations") == [(0,)]
    assert sql(path, "SELECT COUNT(*) FROM pubmed_bibliography_batches") == [(0,)]
    assert store.read(definition).checkpoint_version == 0
    sql(path, "DROP TRIGGER injected_failure")
    assert store.save_bibliography(definition, pending, batch, object_id, NOW).state == "succeeded"


def test_concurrent_exact_retry_commits_once(workspace):
    path, store = workspace
    definition, _ = prepare(path, store)
    pending = store.next_batch(definition, maximum_batch_size=2)
    batch = bibliography(pending)
    object_id = publish(path, batch.raw_body)
    barrier = Barrier(2)

    def worker():
        barrier.wait(timeout=3)
        local = SqlitePubmedHarvestStoreAdapter(lambda: connect(path))
        return local.save_bibliography(definition, pending, batch, object_id, NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        results = [future.result(timeout=5) for future in futures]
    assert results[0] == results[1]
    assert sql(path, "SELECT COUNT(*) FROM source_observations") == [(2,)]
    assert sql(path, "SELECT COUNT(*) FROM pubmed_bibliography_batches") == [(1,)]
    assert store.read(definition).checkpoint_version == 1


def test_cross_binding_batch_cannot_short_circuit_as_replay(workspace):
    path, store, definition, pending, batch, object_id = completed(workspace)
    other = plan("other")
    store.ensure(other, NOW)
    with pytest.raises(HarvestError, match="checkpoint_conflict"):
        store.save_bibliography(other, pending, batch, object_id, NOW)
    assert store.read(other).checkpoint_version == 0
    assert store.read(definition).checkpoint_version == 1


@pytest.mark.parametrize("damage", [
    "DELETE FROM source_observations WHERE native_id='100'",
    "UPDATE source_observations SET native_id='999' WHERE native_id='100'",
    "UPDATE source_observations SET parser_version='other' WHERE native_id='100'",
    "UPDATE source_observations SET id='wrong-id' WHERE native_id='100'",
    "DELETE FROM pubmed_bibliography_batches",
    "UPDATE pubmed_bibliography_batches SET pmids_json='[\"100\"]'",
    "UPDATE pubmed_harvest_pages SET page_fingerprint='bad'",
    "UPDATE pubmed_harvest_pages SET next_batch_offset=1",
    "UPDATE harvest_units SET state='pending'",
    "UPDATE harvest_units SET checkpoint_version=2",
    "UPDATE harvest_units SET coverage_json='{\"complete\":false,\"format_version\":1,\"record_count\":2}'",
    "UPDATE object_registry SET state='quarantined'",
])
def test_corrupt_completed_receipts_never_read_or_replay_as_success(workspace, damage):
    path, store, definition, pending, batch, object_id = completed(workspace)
    sql(path, damage)
    for action in (
        lambda: store.read(definition),
        lambda: store.save_bibliography(definition, pending, batch, object_id, NOW),
    ):
        with pytest.raises(HarvestError):
            action()


def test_missing_earlier_batch_prevents_last_batch_from_advancing(workspace):
    path, store = workspace
    definition, _ = prepare(path, store)
    commit_next(path, store, definition, 1)
    pending = store.next_batch(definition, maximum_batch_size=1)
    batch = bibliography(pending)
    object_id = publish(path, batch.raw_body)
    sql(path, "DELETE FROM source_observations WHERE native_id='100'")
    with pytest.raises(HarvestError):
        store.save_bibliography(definition, pending, batch, object_id, NOW)
    assert sql(path, "SELECT checkpoint_version FROM harvest_units") == [(0,)]
    assert sql(path, "SELECT COUNT(*) FROM pubmed_bibliography_batches") == [(1,)]


@pytest.mark.parametrize("pmids,total", [
    ((), 2), (("100", "100"), 2), (("100", "0100"), 2),
    (("0",), 1), (("x",), 1), (("100",), 0), (("100",), 3),
])
def test_invalid_search_page_does_not_create_durable_stage(workspace, pmids, total):
    path, store = workspace
    definition = plan()
    store.ensure(definition, NOW)
    result = page(definition, pmids, total=total)
    object_id = publish(path, result.raw_body)
    with pytest.raises(HarvestError):
        store.save_search(definition, result, object_id, NOW)
    assert sql(path, "SELECT COUNT(*) FROM pubmed_harvest_pages") == [(0,)]
    assert store.read(definition).checkpoint_version == 0


def test_modified_plan_fingerprint_rejected_before_any_write(workspace):
    path, store = workspace
    tampered = replace(plan(), query_fingerprint="f" * 64)
    with pytest.raises(HarvestError, match="invalid_harvest_definition"):
        store.ensure(tampered, NOW)
    assert sql(path, "SELECT COUNT(*) FROM source_bindings") == [(0,)]


def test_verified_empty_replay_is_stable(workspace):
    path, store = workspace
    definition, result = prepare(path, store, pmids=())
    before = store.read(definition)
    assert (before.state, before.next_start, before.checkpoint_version) == ("verified_empty", 0, 1)
    assert store.save_search(definition, result, publish(path, result.raw_body), NOW) == before


def test_search_replay_after_nonempty_page_advance_is_stable(workspace):
    path, store = workspace
    definition, search = prepare(path, store)
    final, _, _, _ = commit_next(path, store, definition)
    assert store.save_search(definition, search, publish(path, search.raw_body), NOW) == final
