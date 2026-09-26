import hashlib
import sqlite3
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_local_delivery_history_adapter import (
    SqliteLocalDeliveryHistoryAdapter,
)
from libs.delivery.application.queries.read_local_reading_history import (
    ReadLocalReadingHistory,
)
from libs.delivery.exceptions.local_reading_history_error import (
    LocalReadingHistoryError,
)
from libs.kernel.exceptions.storage_error import StorageError
from libs.scholarly_catalog.adapters.driven.sqlite_local_paper_history_adapter import (
    SqliteLocalPaperHistoryAdapter,
)
from libs.scholarly_catalog.application.queries.read_local_paper_record import (
    ReadLocalPaperRecord,
)
from libs.scholarly_catalog.exceptions.local_paper_history_error import (
    LocalPaperHistoryError,
)


NOW = "2026-09-26T00:00:00+00:00"


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return factory


def _object(
    connection: sqlite3.Connection,
    object_id: str,
    *,
    kind: str,
    state: str = "available",
) -> None:
    digest = hashlib.sha256(object_id.encode()).hexdigest()
    connection.execute(
        "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
        (
            object_id,
            digest,
            f"objects/{digest}",
            kind,
            "application/octet-stream",
            1,
            state,
            NOW,
            "test",
        ),
    )


def _setup(tmp_path: Path) -> Path:
    path = tmp_path / "reading.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0001-object-registry.sql",
        "0002-watch-profiles.sql",
        "0003-scholarly-catalog.sql",
        "0005-paper-explanations.sql",
        "0007-delivery.sql",
    ):
        connection.executescript(
            (root / "migrations" / name).read_text(encoding="utf-8")
        )

    _object(connection, "raw:abstract", kind="raw")
    _object(connection, "evidence:one", kind="evidence")
    _object(connection, "extracted:one", kind="extracted")
    _object(connection, "model_output:summary", kind="model_output")
    _object(connection, "digest:paper", kind="digest")
    _object(connection, "digest:status", kind="digest")

    for work_id, title in (
        ("work:canonical", "Canonical title"),
        ("work:old", "Old title"),
    ):
        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                work_id,
                title,
                "published",
                "2026",
                "year",
                NOW,
                NOW,
            ),
        )
    connection.execute(
        "INSERT INTO work_aliases VALUES(?,?,?,?)",
        (
            "work:old",
            "work:canonical",
            '{"reason":"identity-merge"}',
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
        (
            "manifest:doi",
            "work:canonical",
            "doi",
            "10.1000/example",
            "publication",
            "https://doi.org/10.1000/example",
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
        (
            "manifest:arxiv",
            "work:old",
            "arxiv",
            "2609.00001",
            "preprint",
            "https://arxiv.org/abs/2609.00001",
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "revision:doi",
            "manifest:doi",
            "work:canonical",
            None,
            "a" * 64,
            "Canonical title",
            "raw:abstract",
            NOW,
            "2026-09-25",
            "day",
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "revision:arxiv",
            "manifest:arxiv",
            "work:old",
            "1",
            "b" * 64,
            "Preprint title",
            None,
            NOW,
            "2026-09-20",
            "day",
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO access_assessments VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "access:free",
            "manifest:doi",
            "https://example.org/free",
            "free",
            "unknown",
            '["read"]',
            "cc-by",
            '{"content_scope":"full_text"}',
            NOW,
            None,
            "revision:doi",
        ),
    )

    connection.execute(
        "INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "snapshot:one",
            "revision:arxiv",
            "work:old",
            "evidence:one",
            "extracted:one",
            "parser-v1",
            "abstract_only",
            "{}",
            "c" * 64,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO model_runs("
        "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,"
        "state,output_object_id,started_at,finished_at"
        ") VALUES(?,?,?,?,?,?,? ,?,?,?)",
        (
            "run:summary",
            "summary",
            "fixture",
            "fixture",
            "d" * 64,
            "e" * 64,
            "succeeded",
            "model_output:summary",
            NOW,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO summary_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "summary:one",
            "revision:arxiv",
            "work:old",
            "snapshot:one",
            "e" * 64,
            "run:summary",
            "model_output:summary",
            "zh-TW",
            "plain-zh-TW-v1",
            "passed",
            NOW,
        ),
    )

    connection.execute(
        "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
        (
            "event:paper",
            "work:old",
            "revision:arxiv",
            "new_work",
            "event-key:paper",
            "{}",
            NOW,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
        (
            "event:correction",
            "work:canonical",
            None,
            "correction",
            "event-key:correction",
            "{}",
            NOW,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "subscription:email",
            "reader:local",
            "email",
            1,
            "Asia/Taipei",
            "{}",
            10,
            "recipient:test",
            1,
            NOW,
        ),
    )

    connection.execute(
        "INSERT INTO digests VALUES(?,?,?,?,?,?,?)",
        (
            "digest:paper-id",
            "subscription:email",
            "2026-09-25",
            NOW,
            "digest:paper",
            "sent",
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO digest_items VALUES(?,?,?,?,?,?,?)",
        (
            "digest:paper-id",
            1,
            "event:paper",
            "work:old",
            "summary:one",
            "revision:arxiv",
            "paper",
        ),
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES(?,?,?,?,?,?,?,?)",
        (
            "outbox:paper",
            "digest:paper-id",
            "idempotency:paper",
            "f" * 64,
            "provider_accepted",
            1,
            None,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO notification_ledger VALUES(?,?,?,?,?,?,?)",
        (
            "ledger:paper",
            "reader:local",
            "event:paper",
            "email",
            "outbox:paper",
            "accepted",
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO delivery_attempts VALUES(?,?,?,?,?,?,?,?)",
        (
            "attempt:paper",
            "outbox:paper",
            1,
            "provider_accepted",
            "provider:1",
            None,
            NOW,
            NOW,
        ),
    )

    connection.execute(
        "INSERT INTO digests VALUES(?,?,?,?,?,?,?)",
        (
            "digest:status-id",
            "subscription:email",
            "2026-09-26",
            NOW,
            "digest:status",
            "unknown",
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO digest_items VALUES(?,?,?,?,?,?,?)",
        (
            "digest:status-id",
            1,
            "event:correction",
            "work:canonical",
            None,
            None,
            "status_notice",
        ),
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES(?,?,?,?,?,?,?,?)",
        (
            "outbox:status",
            "digest:status-id",
            "idempotency:status",
            "1" * 64,
            "unknown",
            1,
            None,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO notification_ledger VALUES(?,?,?,?,?,?,?)",
        (
            "ledger:status",
            "reader:local",
            "event:correction",
            "email",
            "outbox:status",
            "unknown",
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO delivery_attempts VALUES(?,?,?,?,?,?,?,?)",
        (
            "attempt:status",
            "outbox:status",
            1,
            "unknown",
            None,
            "timeout",
            NOW,
            NOW,
        ),
    )
    connection.commit()
    connection.close()
    return path


class FixtureObjectReader:
    def __call__(self, object_id: str) -> bytes:
        assert isinstance(object_id, str) and object_id
        return b"fixture"


def _query(path: Path) -> ReadLocalReadingHistory:
    connect = _connect(path)
    return ReadLocalReadingHistory(
        ReadLocalPaperRecord(
            SqliteLocalPaperHistoryAdapter(
                connect,
                FixtureObjectReader(),
            )
        ),
        SqliteLocalDeliveryHistoryAdapter(connect),
    )


def test_alias_id_returns_canonical_family_versions_access_and_history(
    tmp_path: Path,
) -> None:
    path = _setup(tmp_path)
    result = _query(path)("work:old", "reader:local")

    assert result.paper.requested_work_id == "work:old"
    assert result.paper.canonical_work_id == "work:canonical"
    assert result.paper.family_work_ids == ("work:canonical", "work:old")
    assert {item.manifestation_id for item in result.paper.manifestations} == {
        "manifest:doi",
        "manifest:arxiv",
    }
    free = [
        access
        for manifestation in result.paper.manifestations
        for access in manifestation.access_assessments
        if access.reader_access == "free"
    ]
    assert [(item.location_url, item.content_scope) for item in free] == [
        ("https://example.org/free", "full_text")
    ]
    assert [(item.event_kind, item.ledger_state) for item in result.notifications] == [
        ("correction", "unknown"),
        ("new_work", "accepted"),
    ]
    paper = next(item for item in result.notifications if item.event_id == "event:paper")
    assert paper.item_kind == "paper"
    assert paper.summary_id == "summary:one"
    assert paper.revision_id == "revision:arxiv"
    assert paper.attempts[0].state == "provider_accepted"


def test_empty_access_and_delivery_are_real_empty_collections(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    result = _query(path)("work:old", "reader:nobody")

    assert result.notifications == ()
    arxiv = next(
        item
        for item in result.paper.manifestations
        if item.manifestation_id == "manifest:arxiv"
    )
    assert arxiv.access_assessments == ()


def test_channel_filter_is_exact(tmp_path: Path) -> None:
    path = _setup(tmp_path)

    email = _query(path)("work:canonical", "reader:local", "email")
    rss = _query(path)("work:canonical", "reader:local", "rss")

    assert len(email.notifications) == 2
    assert rss.notifications == ()


def test_missing_work_is_not_reported_as_empty_history(tmp_path: Path) -> None:
    path = _setup(tmp_path)

    with pytest.raises(LocalPaperHistoryError, match="local_paper_missing"):
        _query(path)("work:missing", "reader:local")


def test_unavailable_referenced_abstract_object_is_integrity_error(
    tmp_path: Path,
) -> None:
    path = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE object_registry SET state='quarantined' "
        "WHERE object_id='raw:abstract'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        LocalPaperHistoryError,
        match="local_paper_object_unavailable",
    ):
        _query(path)("work:canonical", "reader:local")


def test_corrupt_delivery_join_is_not_reported_as_empty(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "DELETE FROM digest_items WHERE digest_id='digest:status-id'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        LocalReadingHistoryError,
        match="local_reading_history_corrupt",
    ):
        _query(path)("work:canonical", "reader:local")


def test_reopen_returns_same_read_model(tmp_path: Path) -> None:
    path = _setup(tmp_path)

    first = _query(path)("work:old", "reader:local")
    second = _query(path)("work:old", "reader:local")

    assert second == first


class MissingObjectReader:
    def __call__(self, object_id: str) -> bytes:
        if object_id == "raw:abstract":
            raise StorageError("missing", object_id)
        return b"fixture"


def test_registry_available_but_missing_referenced_object_is_integrity_error(
    tmp_path: Path,
) -> None:
    path = _setup(tmp_path)
    connect = _connect(path)
    paper = ReadLocalPaperRecord(
        SqliteLocalPaperHistoryAdapter(
            connect,
            MissingObjectReader(),
        )
    )

    with pytest.raises(
        LocalPaperHistoryError,
        match="local_paper_object_unavailable",
    ):
        paper("work:canonical")
