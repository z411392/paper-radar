import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import (
    DeliveryStoreError,
    SqliteDeliveryStoreAdapter,
)
from libs.delivery.dtos.digest_preview import DigestPreview, SelectedDigestItem
from libs.delivery.dtos.delivery_queue import QueueDigestRequest


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
SUBSCRIPTION = "subscription:test"
READER = "reader:test"
CHANNEL = "email"


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path) -> tuple[Path, SqliteDeliveryStoreAdapter]:
    path = tmp_path / "delivery.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE object_registry(
          object_id TEXT PRIMARY KEY, content_sha256 TEXT NOT NULL, kind TEXT NOT NULL,
          state TEXT NOT NULL);
        CREATE TABLE delivery_subscriptions(
          id TEXT PRIMARY KEY, reader_id TEXT NOT NULL, channel TEXT NOT NULL,
          enabled INTEGER NOT NULL, UNIQUE(reader_id,channel));
        CREATE TABLE paper_works(id TEXT PRIMARY KEY);
        CREATE TABLE paper_revisions(
          id TEXT PRIMARY KEY,work_id TEXT NOT NULL,UNIQUE(id,work_id));
        CREATE TABLE research_events(
          id TEXT PRIMARY KEY,work_id TEXT NOT NULL,revision_id TEXT,
          canonical_event_key TEXT NOT NULL UNIQUE,UNIQUE(id,work_id),
          FOREIGN KEY(revision_id,work_id) REFERENCES paper_revisions(id,work_id));
        CREATE TABLE summary_revisions(
          id TEXT PRIMARY KEY,revision_id TEXT NOT NULL,work_id TEXT NOT NULL,
          UNIQUE(id,revision_id,work_id));
        CREATE TABLE digests(
          id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL REFERENCES delivery_subscriptions(id),
          period_key TEXT NOT NULL,cutoff_at TEXT NOT NULL,
          rendered_object_id TEXT REFERENCES object_registry(object_id),
          state TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(subscription_id,period_key));
        CREATE TABLE digest_items(
          digest_id TEXT NOT NULL REFERENCES digests(id),position INTEGER NOT NULL,
          event_id TEXT NOT NULL REFERENCES research_events(id),
          work_id TEXT NOT NULL REFERENCES paper_works(id),summary_id TEXT,revision_id TEXT,
          item_kind TEXT NOT NULL,PRIMARY KEY(digest_id,position),UNIQUE(digest_id,event_id),
          FOREIGN KEY(summary_id,revision_id,work_id) REFERENCES summary_revisions(id,revision_id,work_id),
          FOREIGN KEY(event_id,work_id) REFERENCES research_events(id,work_id));
        CREATE TABLE delivery_outbox(
          id TEXT PRIMARY KEY,digest_id TEXT NOT NULL UNIQUE REFERENCES digests(id),
          idempotency_key TEXT NOT NULL UNIQUE,payload_sha256 TEXT NOT NULL,
          state TEXT NOT NULL,workspace_epoch INTEGER NOT NULL,next_attempt_at TEXT,
          created_at TEXT NOT NULL);
        CREATE TABLE notification_ledger(
          id TEXT PRIMARY KEY,reader_id TEXT NOT NULL,event_id TEXT NOT NULL REFERENCES research_events(id),
          channel TEXT NOT NULL,outbox_id TEXT NOT NULL REFERENCES delivery_outbox(id),
          state TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(reader_id,event_id,channel));
        """
    )
    connection.execute(
        "INSERT INTO delivery_subscriptions VALUES(?,?,?,1)",
        (SUBSCRIPTION, READER, CHANNEL),
    )
    connection.commit()
    connection.close()
    return path, SqliteDeliveryStoreAdapter(_connect(path))


def _seed_item(
    path: Path,
    *,
    event_id: str,
    work_id: str,
    revision_id: str,
    summary_id: str,
) -> None:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("INSERT OR IGNORE INTO paper_works(id) VALUES(?)", (work_id,))
    connection.execute(
        "INSERT OR IGNORE INTO paper_revisions(id,work_id) VALUES(?,?)",
        (revision_id, work_id),
    )
    connection.execute(
        "INSERT OR IGNORE INTO research_events(id,work_id,revision_id,canonical_event_key) VALUES(?,?,?,?)",
        (event_id, work_id, revision_id, "key:" + event_id),
    )
    connection.execute(
        "INSERT OR IGNORE INTO summary_revisions(id,revision_id,work_id) VALUES(?,?,?)",
        (summary_id, revision_id, work_id),
    )
    connection.commit()
    connection.close()


def _object(path: Path, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    object_id = "digest:" + digest
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?)",
        (object_id, digest, "digest", "available"),
    )
    connection.commit()
    connection.close()
    return object_id


def _preview(
    *,
    period: str,
    event_id: str,
    work_id: str,
    revision_id: str,
    summary_id: str,
) -> DigestPreview:
    item = SelectedDigestItem(
        event_id=event_id,
        work_id=work_id,
        summary_id=summary_id,
        revision_id=revision_id,
        event_at=NOW,
        priority=10,
        domains=("statistics",),
        title="A paper",
        source_url="https://example.org/paper",
        plain_language=("重點",),
    )
    return DigestPreview(
        subscription_id=SUBSCRIPTION,
        period_key=period,
        cutoff_at=NOW,
        queueable=True,
        items=(item,),
        subject="Paper Radar｜每日精選 1 篇",
        text_body="text",
        html_body="<p>html</p>",
        content_fingerprint=hashlib.sha256(period.encode()).hexdigest(),
    )


def _request(preview: DigestPreview, object_id: str) -> QueueDigestRequest:
    return QueueDigestRequest(
        preview=preview,
        reader_id=READER,
        channel=CHANNEL,
        rendered_object_id=object_id,
        workspace_epoch=1,
        created_at=NOW,
    )


def _counts(path: Path) -> tuple[int, int, int, int]:
    connection = sqlite3.connect(path)
    values = tuple(
        connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("digests", "digest_items", "delivery_outbox", "notification_ledger")
    )
    connection.close()
    return values


def test_queue_saves_digest_item_outbox_and_ledger_atomically(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _seed_item(
        path,
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    preview = _preview(
        period="2026-09-23",
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    object_id = _object(path, b"frozen digest")

    queued = store.queue(_request(preview, object_id))

    assert queued.replayed is False
    assert queued.payload_sha256 == object_id.split(":", 1)[1]
    assert _counts(path) == (1, 1, 1, 1)


def test_exact_replay_returns_same_business_notification(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _seed_item(
        path,
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    preview = _preview(
        period="2026-09-23",
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    request = _request(preview, _object(path, b"frozen digest"))

    first = store.queue(request)
    second = store.queue(request)

    assert second.replayed is True
    assert second.digest_id == first.digest_id
    assert second.outbox_id == first.outbox_id
    assert second.idempotency_key == first.idempotency_key
    assert _counts(path) == (1, 1, 1, 1)


def test_correction_event_for_same_work_is_a_distinct_notification(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    for event_id, revision_id, summary_id in (
        ("event:new", "revision:1", "summary:1"),
        ("event:correction", "revision:2", "summary:2"),
    ):
        _seed_item(
            path,
            event_id=event_id,
            work_id="work:1",
            revision_id=revision_id,
            summary_id=summary_id,
        )
    first = _preview(
        period="2026-09-23",
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    correction = _preview(
        period="2026-09-24",
        event_id="event:correction",
        work_id="work:1",
        revision_id="revision:2",
        summary_id="summary:2",
    )

    store.queue(_request(first, _object(path, b"first")))
    store.queue(_request(correction, _object(path, b"correction")))

    assert _counts(path) == (2, 2, 2, 2)


def test_same_event_in_another_digest_is_rejected_as_stale_selection(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _seed_item(
        path,
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    first = _preview(
        period="2026-09-23",
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    later = _preview(
        period="2026-09-24",
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    store.queue(_request(first, _object(path, b"first")))

    with pytest.raises(DeliveryStoreError, match="event_already_notified"):
        store.queue(_request(later, _object(path, b"later")))

    assert _counts(path) == (1, 1, 1, 1)


def test_same_period_with_different_payload_conflicts_instead_of_overwriting(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _seed_item(
        path,
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    preview = _preview(
        period="2026-09-23",
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    store.queue(_request(preview, _object(path, b"first")))

    with pytest.raises(DeliveryStoreError, match="digest_period_conflict"):
        store.queue(_request(preview, _object(path, b"changed")))

    assert _counts(path) == (1, 1, 1, 1)


def test_failure_while_writing_ledger_rolls_back_every_database_row(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _seed_item(
        path,
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    preview = _preview(
        period="2026-09-23",
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    object_id = _object(path, b"frozen digest")
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TRIGGER fail_ledger BEFORE INSERT ON notification_ledger "
        "BEGIN SELECT RAISE(ABORT,'forced ledger failure'); END"
    )
    connection.commit()
    connection.close()

    with pytest.raises(DeliveryStoreError, match="delivery_database_error"):
        store.queue(_request(preview, object_id))

    assert _counts(path) == (0, 0, 0, 0)


def test_concurrent_identical_queue_creates_one_outbox_and_one_ledger(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    _seed_item(
        path,
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    preview = _preview(
        period="2026-09-23",
        event_id="event:new",
        work_id="work:1",
        revision_id="revision:1",
        summary_id="summary:1",
    )
    request = _request(preview, _object(path, b"frozen digest"))
    gate = threading.Barrier(2)
    results = []
    errors = []

    def worker() -> None:
        try:
            gate.wait(timeout=2)
            results.append(store.queue(request))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert len(results) == 2
    assert {result.replayed for result in results} == {False, True}
    assert len({result.outbox_id for result in results}) == 1
    assert _counts(path) == (1, 1, 1, 1)
