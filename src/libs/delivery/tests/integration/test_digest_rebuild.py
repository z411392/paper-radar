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
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)


NOW = datetime(2026, 9, 25, 11, 0, tzinfo=timezone.utc)
WORK = "work:status"


def _object(connection: sqlite3.Connection, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    object_id = "digest:" + digest
    connection.execute(
        "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
        (
            object_id,
            digest,
            f"objects/digest/{digest[:2]}/{digest}",
            "digest",
            "application/json",
            len(content),
            "available",
            NOW.isoformat(),
            "delivery",
        ),
    )
    return object_id


def _setup(tmp_path: Path):
    root = tmp_path / "workspace"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 23
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=23)
    connection = schema.connect()
    try:
        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "Status paper",
                "published",
                None,
                None,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        for event_id, kind in (
            ("event:correction", "correction"),
            ("event:retraction", "retraction"),
        ):
            connection.execute(
                "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    WORK,
                    None,
                    kind,
                    "key:" + event_id,
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
        first_object = _object(connection, b"first")
        second_object = _object(connection, b"second")
        connection.commit()
    finally:
        connection.close()
    return root, schema, SqliteDeliveryStoreAdapter(schema.connect), first_object, second_object


def _preview(event_id: str, *, marker: str) -> DigestPreview:
    kind = "correction" if event_id.endswith("correction") else "retraction"
    item = SelectedDigestItem(
        event_id=event_id,
        work_id=WORK,
        summary_id=None,
        revision_id=None,
        event_at=NOW,
        priority=100,
        domains=(),
        title="Status paper",
        source_url=None,
        plain_language=(marker,),
        item_kind="status_notice",
        event_kind=kind,
    )
    return DigestPreview(
        "subscription:test",
        "2026-09-25",
        NOW,
        True,
        (item,),
        "Paper Radar status",
        marker,
        "<p>" + marker + "</p>",
        hashlib.sha256(marker.encode()).hexdigest(),
    )


def _request(
    preview: DigestPreview,
    object_id: str,
    *,
    rebuild_reason: str | None = None,
    rebuild_outbox_id: str | None = None,
) -> QueueDigestRequest:
    return QueueDigestRequest(
        preview,
        "reader:test",
        "email",
        object_id,
        1,
        NOW,
        rebuild_reason,
        rebuild_outbox_id,
    )


def test_cancelled_never_dispatched_slot_rebuilds_with_append_only_audit(
    tmp_path: Path,
) -> None:
    root, schema, store, first_object, second_object = _setup(tmp_path)
    first = store.queue(_request(_preview("event:correction", marker="first"), first_object))
    assert store.cancel_pending(first.outbox_id) == "cancelled"

    rebuilt = store.queue(
        _request(
            _preview("event:correction", marker="second"),
            second_object,
            rebuild_reason="current_input_stale",
            rebuild_outbox_id=first.outbox_id,
        )
    )
    replay = store.queue(
        _request(_preview("event:correction", marker="second"), second_object)
    )

    assert rebuilt.digest_id == first.digest_id
    assert rebuilt.outbox_id == first.outbox_id
    assert rebuilt.replayed is False
    assert replay.replayed is True
    connection = schema.connect()
    try:
        audit = connection.execute(
            "SELECT generation,prior_rendered_object_id,prior_payload_sha256,"
            "prior_idempotency_key,reason FROM delivery_digest_rebuilds"
        ).fetchone()
        assert audit[0] == 1
        assert audit[1] == first_object
        assert audit[2] == first.payload_sha256
        assert audit[3] == first.idempotency_key
        assert audit[4] == "current_input_stale"
        current = connection.execute(
            "SELECT d.rendered_object_id,d.state,o.payload_sha256,o.idempotency_key,"
            "o.state FROM digests d JOIN delivery_outbox o ON o.digest_id=d.id"
        ).fetchone()
        assert current[0] == second_object
        assert current[1] == "queued"
        assert current[2] == second_object.split(":", 1)[1]
        assert current[3] == rebuilt.idempotency_key
        assert current[4] == "pending"
        assert connection.execute(
            "SELECT state FROM notification_ledger WHERE event_id='event:correction'"
        ).fetchone()[0] == "reserved"
    finally:
        connection.close()


def test_rebuild_can_replace_event_without_resurrecting_removed_ledger(
    tmp_path: Path,
) -> None:
    _, schema, store, first_object, second_object = _setup(tmp_path)
    first = store.queue(_request(_preview("event:correction", marker="first"), first_object))
    store.cancel_pending(first.outbox_id)

    store.queue(
        _request(
            _preview("event:retraction", marker="second"),
            second_object,
            rebuild_reason="current_input_stale",
            rebuild_outbox_id=first.outbox_id,
        )
    )

    connection = schema.connect()
    try:
        ledgers = dict(
            connection.execute(
                "SELECT event_id,state FROM notification_ledger ORDER BY event_id"
            ).fetchall()
        )
        assert ledgers == {
            "event:correction": "cancelled",
            "event:retraction": "reserved",
        }
        assert connection.execute(
            "SELECT event_id FROM digest_items"
        ).fetchone()[0] == "event:retraction"
    finally:
        connection.close()


def test_cancelled_slot_with_any_delivery_attempt_cannot_rebuild(tmp_path: Path) -> None:
    _, schema, store, first_object, second_object = _setup(tmp_path)
    first = store.queue(_request(_preview("event:correction", marker="first"), first_object))
    store.cancel_pending(first.outbox_id)
    connection = schema.connect()
    try:
        connection.execute(
            "INSERT INTO delivery_attempts("
            "id,outbox_id,attempt_no,state,error_code,started_at,finished_at"
            ") VALUES(?,?,1,'failed','fixture',?,?)",
            (
                "attempt:historical",
                first.outbox_id,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DeliveryStoreError, match="digest_rebuild_attempt_exists"):
        store.queue(
            _request(
                _preview("event:correction", marker="second"),
                second_object,
                rebuild_reason="current_input_stale",
                rebuild_outbox_id=first.outbox_id,
            )
        )


def test_rebuild_audit_rows_are_immutable(tmp_path: Path) -> None:
    _, schema, store, first_object, second_object = _setup(tmp_path)
    first = store.queue(_request(_preview("event:correction", marker="first"), first_object))
    store.cancel_pending(first.outbox_id)
    store.queue(
        _request(
            _preview("event:correction", marker="second"),
            second_object,
            rebuild_reason="current_input_stale",
            rebuild_outbox_id=first.outbox_id,
        )
    )

    connection = schema.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="delivery_digest_rebuild_immutable"):
            connection.execute(
                "UPDATE delivery_digest_rebuilds SET reason='forged'"
            )
    finally:
        connection.close()


def test_cancelled_slot_without_explicit_rebuild_authority_stays_cancelled(
    tmp_path: Path,
) -> None:
    _, schema, store, first_object, second_object = _setup(tmp_path)
    first = store.queue(
        _request(_preview("event:correction", marker="first"), first_object)
    )
    store.cancel_pending(first.outbox_id)

    with pytest.raises(DeliveryStoreError, match="digest_rebuild_not_authorized"):
        store.queue(
            _request(_preview("event:correction", marker="second"), second_object)
        )

    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT state FROM delivery_outbox"
        ).fetchone()[0] == "cancelled"
        assert connection.execute(
            "SELECT count(*) FROM delivery_digest_rebuilds"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_rebuild_requires_exact_cancelled_outbox_authority(tmp_path: Path) -> None:
    _, _, store, first_object, second_object = _setup(tmp_path)
    first = store.queue(
        _request(_preview("event:correction", marker="first"), first_object)
    )
    store.cancel_pending(first.outbox_id)

    with pytest.raises(DeliveryStoreError, match="digest_rebuild_target_mismatch"):
        store.queue(
            _request(
                _preview("event:correction", marker="second"),
                second_object,
                rebuild_reason="current_input_stale",
                rebuild_outbox_id="outbox:wrong",
            )
        )


def test_rebuild_outbox_without_reason_is_rejected(tmp_path: Path) -> None:
    _, _, store, first_object, _ = _setup(tmp_path)

    with pytest.raises(DeliveryStoreError, match="invalid_digest_rebuild_target"):
        store.queue(
            _request(
                _preview("event:correction", marker="first"),
                first_object,
                rebuild_outbox_id="outbox:unexpected",
            )
        )
