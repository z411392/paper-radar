import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import (
    SqliteDeliveryStoreAdapter,
)
from libs.delivery.dtos.delivery_queue import QueueDigestRequest
from libs.delivery.dtos.digest_preview import DigestPreview, SelectedDigestItem
from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError


NOW = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return factory


def _setup(tmp_path: Path):
    root = Path(__file__).resolve().parents[5]
    path = tmp_path / "status.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for version in range(1, 8):
        names = sorted((root / "migrations").glob(f"{version:04d}-*.sql"))
        assert len(names) == 1
        connection.executescript(names[0].read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO workspace_metadata VALUES(1,'workspace:test',1,0,?,NULL)",
        (NOW.isoformat(),),
    )
    connection.execute(
        "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
        (
            "work:status",
            "Status paper",
            "published",
            None,
            None,
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
        (
            "event:correction",
            "work:status",
            None,
            "correction",
            "event-key:correction",
            '{"provider":"crossref"}',
            None,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "subscription:test",
            "reader:test",
            "email",
            1,
            "Asia/Taipei",
            '{"kind":"daily","local_time":"08:00"}',
            5,
            "recipient:test",
            1,
            NOW.isoformat(),
        ),
    )
    payload = b"status digest"
    digest = hashlib.sha256(payload).hexdigest()
    object_id = "digest:" + digest
    connection.execute(
        "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
        (
            object_id,
            digest,
            "objects/digest/test",
            "digest",
            "application/json",
            len(payload),
            "available",
            NOW.isoformat(),
            "delivery",
        ),
    )
    connection.commit()
    connection.close()
    return path, object_id, SqliteDeliveryStoreAdapter(_connect(path))


def _preview(*, event_kind: str = "correction") -> DigestPreview:
    item = SelectedDigestItem(
        event_id="event:correction",
        work_id="work:status",
        summary_id=None,
        revision_id=None,
        event_at=NOW,
        priority=100,
        domains=(),
        title="Status paper",
        source_url=None,
        plain_language=("來源目前回報這篇研究有更正紀錄。",),
        item_kind="status_notice",
        event_kind=event_kind,
    )
    return DigestPreview(
        "subscription:test",
        "2026-09-25",
        NOW,
        True,
        (item,),
        "Paper Radar｜研究狀態更新 1 則",
        "text",
        "<p>html</p>",
        "a" * 64,
    )


def _request(preview: DigestPreview, object_id: str) -> QueueDigestRequest:
    return QueueDigestRequest(
        preview,
        "reader:test",
        "email",
        object_id,
        1,
        NOW,
    )


def test_queue_persists_revisionless_status_notice_and_existing_ledger_identity(
    tmp_path: Path,
) -> None:
    path, object_id, store = _setup(tmp_path)
    request = _request(_preview(), object_id)

    first = store.queue(request)
    replay = store.queue(request)

    assert first.replayed is False
    assert replay.replayed is True
    connection = sqlite3.connect(path)
    item = connection.execute(
        "SELECT event_id,work_id,summary_id,revision_id,item_kind "
        "FROM digest_items"
    ).fetchone()
    ledger = connection.execute(
        "SELECT reader_id,event_id,channel,state FROM notification_ledger"
    ).fetchone()
    connection.close()
    assert item == (
        "event:correction",
        "work:status",
        None,
        None,
        "status_notice",
    )
    assert ledger == ("reader:test", "event:correction", "email", "reserved")


def test_queue_rejects_forged_status_item_kind_before_writing_rows(
    tmp_path: Path,
) -> None:
    path, object_id, store = _setup(tmp_path)

    with pytest.raises(DeliveryStoreError, match="invalid_digest_items"):
        store.queue(_request(_preview(event_kind="new_work"), object_id))

    connection = sqlite3.connect(path)
    assert connection.execute("SELECT count(*) FROM digests").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM notification_ledger").fetchone()[0] == 0
    connection.close()
