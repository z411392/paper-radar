import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.pubmed_source_adapter import PubmedSourceAdapter
from libs.discovery.adapters.driven.sqlite_pubmed_harvest_store_adapter import (
    SqlitePubmedHarvestStoreAdapter,
)
from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot
from libs.discovery.dtos.pubmed_bibliography_batch import PubmedBibliographyBatch
from libs.discovery.dtos.pubmed_bibliography_record import PubmedBibliographyRecord
from libs.discovery.dtos.pubmed_search_page import PubmedSearchPage
from libs.discovery.dtos.source_page_observation import SourcePageObservation
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.exceptions.harvest_error import HarvestError


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "pubmed.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    root = Path(__file__).resolve().parents[5]
    for name in (
        "0001-object-registry.sql",
        "0002-watch-profiles.sql",
        "0003-scholarly-catalog.sql",
        "0004-discovery.sql",
        "0010-pubmed-harvest.sql",
    ):
        connection.executescript((root / "migrations" / name).read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
        ("personal", "reader:local", "mine", "active", None, NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO watch_profile_revisions VALUES(?,?,?,?,?,?)",
        (
            "personal",
            1,
            "scope",
            '{"sources":["pubmed"],"include":[],"exclude":[],"languages":[],'
            '"free_only":false,"allow_preprints":true}',
            "a" * 64,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "UPDATE watch_profiles SET published_revision=1 WHERE id='personal'"
    )
    connection.execute(
        "INSERT INTO domain_definitions VALUES(?,?,?,?,?)",
        (
            "badminton",
            "羽球",
            '{"sources":["pubmed"]}',
            1,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO watch_profile_domains VALUES(?,?,?,?)",
        ("personal", 1, "badminton", 1),
    )
    connection.commit()
    connection.close()
    return path, SqlitePubmedHarvestStoreAdapter(_connect(path))


def query() -> SourceQueryInput:
    return SourceQueryInput(
        "pubmed",
        "personal",
        1,
        "a" * 64,
        DomainQuerySnapshot(
            "badminton",
            1,
            ("pubmed",),
            (),
            ("badminton",),
            (),
            (),
        ),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
        datetime(2026, 9, 24, tzinfo=timezone.utc),
        ("pubmed",),
        deferred_mode="defer",
        time_basis="createDate",
        page_size=2,
    )


def _object(path: Path, kind: str, body: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    object_id = f"{kind}:{digest}"
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
        (
            object_id,
            digest,
            f"objects/{kind}/{digest[:2]}/{digest}",
            kind,
            "application/octet-stream",
            len(body),
            "available",
            NOW.isoformat(),
            "test",
        ),
    )
    connection.commit()
    connection.close()
    return object_id


def search_page(plan, *pmids: str, total: int | None = None) -> PubmedSearchPage:
    values = tuple(pmids)
    total = len(values) if total is None else total
    body = ("search:" + ",".join(values)).encode()
    digest = hashlib.sha256(body).hexdigest()
    request = PubmedSourceAdapter(
        tool="paper-radar",
        email="reader@example.com",
    ).page(plan, 0)
    return PubmedSearchPage(
        SourcePageObservation("pubmed", plan.query_fingerprint, 0, total, values),
        values,
        body,
        digest,
        request.request_fingerprint,
        "pubmed-eutils-parser-v1",
    )


def bibliography(request_fingerprint: str, *pmids: str) -> PubmedBibliographyBatch:
    records = tuple(
        PubmedBibliographyRecord(
            pmid,
            f"Paper {pmid}",
            "Abstract",
            ("Author",),
            "Journal",
            "2026-Sep-20",
            "day",
            None,
            None,
            ("eng",),
            ("Journal Article",),
        )
        for pmid in pmids
    )
    body = ("bibliography:" + ",".join(pmids)).encode()
    return PubmedBibliographyBatch(
        records,
        body,
        hashlib.sha256(body).hexdigest(),
        request_fingerprint,
        "pubmed-eutils-parser-v1",
    )


def test_partial_bibliography_does_not_advance_page_checkpoint(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    compiler = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    plan = compiler.compile(query())
    store.ensure(plan, NOW)
    page = search_page(plan, "100", "200")
    store.save_search(plan, page, _object(path, "raw", page.raw_body), NOW)

    first = store.next_batch(plan, maximum_batch_size=1)
    assert first is not None and first.pmids == ("100",)
    request = compiler.bibliography_request(first.pmids)
    batch = bibliography(request.request_fingerprint, *first.pmids)
    state = store.save_bibliography(
        plan,
        first,
        batch,
        _object(path, "raw", batch.raw_body),
        NOW,
    )

    assert state.next_start == 0
    assert state.next_batch_offset == 1
    assert state.state == "pending"
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM source_observations").fetchone()[0] == 1
    assert connection.execute(
        "SELECT checkpoint_version FROM harvest_units"
    ).fetchone()[0] == 0
    connection.close()


def test_restart_resumes_remaining_batch_then_advances_checkpoint(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    compiler = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    plan = compiler.compile(query())
    store.ensure(plan, NOW)
    page = search_page(plan, "100", "200")
    store.save_search(plan, page, _object(path, "raw", page.raw_body), NOW)

    first = store.next_batch(plan, maximum_batch_size=1)
    assert first is not None
    first_request = compiler.bibliography_request(first.pmids)
    first_batch = bibliography(first_request.request_fingerprint, *first.pmids)
    store.save_bibliography(
        plan,
        first,
        first_batch,
        _object(path, "raw", first_batch.raw_body),
        NOW,
    )

    restarted = SqlitePubmedHarvestStoreAdapter(_connect(path))
    second = restarted.next_batch(plan, maximum_batch_size=1)
    assert second is not None and second.pmids == ("200",)
    second_request = compiler.bibliography_request(second.pmids)
    second_batch = bibliography(second_request.request_fingerprint, *second.pmids)
    state = restarted.save_bibliography(
        plan,
        second,
        second_batch,
        _object(path, "raw", second_batch.raw_body),
        NOW,
    )

    assert state.next_start == 2
    assert state.state == "succeeded"
    assert state.checkpoint_version == 1
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM source_observations").fetchone()[0] == 2
    payload_kinds = connection.execute(
        "SELECT DISTINCT o.kind FROM source_observations s "
        "JOIN object_registry o ON o.object_id=s.payload_object_id"
    ).fetchall()
    assert payload_kinds == [("raw",)]
    connection.close()


def test_changed_search_identity_is_rejected_without_checkpoint_change(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    compiler = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    plan = compiler.compile(query())
    store.ensure(plan, NOW)
    first = search_page(plan, "100", "200")
    store.save_search(plan, first, _object(path, "raw", first.raw_body), NOW)
    changed = search_page(plan, "100", "300")

    with pytest.raises(HarvestError, match="pubmed_result_set_changed"):
        store.save_search(plan, changed, _object(path, "raw", changed.raw_body), NOW)

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT checkpoint_version FROM harvest_units"
    ).fetchone()[0] == 0
    connection.close()


def test_bibliography_missing_requested_pmid_is_rejected(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    compiler = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    plan = compiler.compile(query())
    store.ensure(plan, NOW)
    page = search_page(plan, "100", "200")
    store.save_search(plan, page, _object(path, "raw", page.raw_body), NOW)
    pending = store.next_batch(plan, maximum_batch_size=2)
    assert pending is not None
    request = compiler.bibliography_request(pending.pmids)
    incomplete = bibliography(request.request_fingerprint, "100")

    with pytest.raises(HarvestError, match="pubmed_batch_identity_mismatch"):
        store.save_bibliography(
            plan,
            pending,
            incomplete,
            _object(path, "raw", incomplete.raw_body),
            NOW,
        )

    assert store.read(plan).next_start == 0


def test_last_batch_exact_replay_after_checkpoint_advance_is_idempotent(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    compiler = PubmedSourceAdapter(tool="paper-radar", email="reader@example.com")
    plan = compiler.compile(query())
    store.ensure(plan, NOW)
    page = search_page(plan, "100")
    store.save_search(plan, page, _object(path, "raw", page.raw_body), NOW)
    pending = store.next_batch(plan, maximum_batch_size=10)
    assert pending is not None
    request = compiler.bibliography_request(pending.pmids)
    batch = bibliography(request.request_fingerprint, *pending.pmids)
    object_id = _object(path, "raw", batch.raw_body)

    first = store.save_bibliography(plan, pending, batch, object_id, NOW)
    replay = store.save_bibliography(plan, pending, batch, object_id, NOW)

    assert first.state == replay.state == "succeeded"
    assert first.checkpoint_version == replay.checkpoint_version == 1
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM source_observations").fetchone()[0] == 1
    assert connection.execute(
        "SELECT count(*) FROM pubmed_bibliography_batches"
    ).fetchone()[0] == 1
    connection.close()
