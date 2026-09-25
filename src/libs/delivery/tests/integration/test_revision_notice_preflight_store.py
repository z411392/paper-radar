import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import (
    SqliteDeliveryStoreAdapter,
)
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


NOW = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
WORK = "work:paper"
OLD_REVISION = "revision:old"
NEW_REVISION = "revision:new"
OLD_SUMMARY = "summary:old"
NEW_SUMMARY = "summary:new"


def _object(connection: sqlite3.Connection, kind: str, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    object_id = f"{kind}:{digest}"
    connection.execute(
        "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
        (
            object_id,
            digest,
            f"objects/{kind}/{digest[:2]}/{digest}",
            kind,
            "application/json",
            len(content),
            "available",
            NOW.isoformat(),
            "test",
        ),
    )
    return object_id


def _seed(root: Path, *, current_summary: str) -> SqliteDeliveryStoreAdapter:
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 22
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=22)
    connection = schema.connect()
    try:
        connection.execute(
            "UPDATE workspace_metadata SET external_effects_enabled=1 WHERE singleton=1"
        )
        evidence = _object(connection, "evidence", b"evidence")
        extracted = _object(connection, "extracted", b"text")
        old_output = _object(connection, "model_output", b"old")
        new_output = _object(connection, "model_output", b"new")
        digest_object = _object(connection, "digest", b"digest")

        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "Paper",
                "published",
                None,
                None,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
            (
                "manifest:paper",
                WORK,
                "fixture",
                "paper",
                "publication",
                "https://example.org/paper",
                NOW.isoformat(),
            ),
        )
        for revision, fingerprint in (
            (OLD_REVISION, "1" * 64),
            (NEW_REVISION, "2" * 64),
        ):
            connection.execute(
                "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    revision,
                    "manifest:paper",
                    WORK,
                    None,
                    fingerprint,
                    "Paper",
                    None,
                    None,
                    "2026-09-25",
                    "day",
                    NOW.isoformat(),
                ),
            )
        for index, revision in enumerate((OLD_REVISION, NEW_REVISION), start=1):
            connection.execute(
                "INSERT INTO evidence_snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    f"snapshot:{index}",
                    revision,
                    WORK,
                    evidence,
                    extracted,
                    "parser:test",
                    "abstract_only",
                    "{}",
                    str(index + 2) * 64,
                    NOW.isoformat(),
                ),
            )
        for index, (summary, revision, output) in enumerate(
            (
                (OLD_SUMMARY, OLD_REVISION, old_output),
                (NEW_SUMMARY, NEW_REVISION, new_output),
            ),
            start=1,
        ):
            generation = str(index + 4) * 64
            run_id = f"run:{index}"
            connection.execute(
                "INSERT INTO model_runs("
                "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,"
                "state,output_object_id,started_at,finished_at"
                ") VALUES(?,?,?,?,?,?,'succeeded',?,?,?)",
                (
                    run_id,
                    "abstract_reading_card",
                    "fixture",
                    "model",
                    str(index + 6) * 64,
                    generation,
                    output,
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO summary_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    summary,
                    revision,
                    WORK,
                    f"snapshot:{index}",
                    generation,
                    run_id,
                    output,
                    "zh-TW",
                    "plain-zh-TW-v1",
                    "passed",
                    NOW.isoformat(),
                ),
            )

        revision_for_current = (
            OLD_REVISION if current_summary == OLD_SUMMARY else NEW_REVISION
        )
        generation_for_current = (
            "5" * 64 if current_summary == OLD_SUMMARY else "6" * 64
        )
        connection.execute(
            "INSERT INTO current_summaries VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "zh-TW",
                "plain-zh-TW-v1",
                current_summary,
                revision_for_current,
                generation_for_current,
                1,
            ),
        )
        connection.execute(
            "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
            (
                "event:paper",
                WORK,
                OLD_REVISION,
                "new_work",
                "event-key:paper",
                "{}",
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
        connection.execute(
            "INSERT INTO digests VALUES(?,?,?,?,?,'queued',?)",
            (
                "digest:test",
                "subscription:test",
                "2026-09-25",
                NOW.isoformat(),
                digest_object,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO digest_items VALUES(?,?,?,?,?,?,?)",
            (
                "digest:test",
                1,
                "event:paper",
                WORK,
                OLD_SUMMARY,
                OLD_REVISION,
                "paper",
            ),
        )
        connection.execute(
            "INSERT INTO delivery_outbox VALUES(?,?,?,?, 'pending',1,NULL,?)",
            (
                "outbox:test",
                "digest:test",
                "delivery-request:test",
                digest_object.split(":", 1)[1],
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO notification_ledger VALUES(?,?,?,?,?,'reserved',?)",
            (
                "notification:test",
                "reader:test",
                "event:paper",
                "email",
                "outbox:test",
                NOW.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return SqliteDeliveryStoreAdapter(schema.connect)


def test_claim_dispatch_cancels_stale_current_summary_without_attempt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    store = _seed(root, current_summary=NEW_SUMMARY)

    claim = store.claim_dispatch("outbox:test", NOW)

    assert claim.state == "cancelled"
    migrations = load_workspace_migrations(with_runtime=True)
    connection = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=22,
    ).connect()
    try:
        assert connection.execute(
            "SELECT state FROM delivery_outbox WHERE id='outbox:test'"
        ).fetchone()[0] == "cancelled"
        assert connection.execute(
            "SELECT state FROM digests WHERE id='digest:test'"
        ).fetchone()[0] == "cancelled"
        assert connection.execute(
            "SELECT state FROM notification_ledger WHERE id='notification:test'"
        ).fetchone()[0] == "cancelled"
        assert connection.execute(
            "SELECT count(*) FROM delivery_attempts"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_claim_dispatch_enters_sending_only_for_exact_current_summary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    store = _seed(root, current_summary=OLD_SUMMARY)

    claim = store.claim_dispatch("outbox:test", NOW)

    assert claim.state == "sending"
    assert claim.attempt_no == 1
    assert claim.attempt_id is not None


def test_cancel_pending_refuses_any_existing_delivery_attempt(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    store = _seed(root, current_summary=OLD_SUMMARY)
    migrations = load_workspace_migrations(with_runtime=True)
    connection = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=22,
    ).connect()
    try:
        connection.execute(
            "INSERT INTO delivery_attempts("
            "id,outbox_id,attempt_no,state,started_at,finished_at,error_code"
            ") VALUES(?,?,1,'failed',?,?,?)",
            (
                "attempt:historical",
                "outbox:test",
                NOW.isoformat(),
                NOW.isoformat(),
                "fixture",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        DeliveryStoreError,
        match="delivery_cancel_attempt_exists",
    ):
        store.cancel_pending("outbox:test")
