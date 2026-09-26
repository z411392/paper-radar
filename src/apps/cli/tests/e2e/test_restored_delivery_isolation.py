import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import (
    SqliteDeliveryStoreAdapter,
)
from libs.delivery.application.commands.dispatch_digest import DispatchDigest
from libs.delivery.dtos.delivery_dispatch import MailSendResult
from libs.delivery.dtos.digest_artifact import StoredDigestPayload
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import (
    FilesystemObjectBytesAdapter,
)
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.adapters.driven.sqlite_workspace_external_effects_adapter import (
    SqliteWorkspaceExternalEffectsAdapter,
)
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.commands.set_workspace_external_effects import (
    SetWorkspaceExternalEffects,
)
from libs.research_workflow.adapters.driven.local_workspace_backup_adapter import (
    LocalWorkspaceBackupAdapter,
    LocalWorkspaceRestoreAdapter,
)
from libs.research_workflow.application.commands.backup_workspace import BackupWorkspace
from libs.research_workflow.application.commands.restore_workspace import RestoreWorkspace


NOW = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
READER = "reader:restore"
SUBSCRIPTION = "subscription:restore"


class FakeDigestReader:
    def __init__(self) -> None:
        self.payloads: dict[str, StoredDigestPayload] = {}

    def read(self, object_id: str) -> StoredDigestPayload:
        return self.payloads[object_id]


class FakeRecipientResolver:
    def resolve(self, recipient_ref: str) -> str | None:
        assert recipient_ref == "recipient:restore"
        return "reader@example.test"


class FakeSender:
    def __init__(self) -> None:
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return MailSendResult(
            "provider_accepted",
            f"provider:{len(self.messages)}",
            None,
        )


def _workspace(tmp_path: Path):
    migrations = load_workspace_migrations(with_runtime=True)
    root = tmp_path / "source"
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == len(migrations)
    schema = SqliteSchemaConnectionFactory(root, migrations)
    return root, migrations, schema


def _publish_digest(root: Path, suffix: str):
    schema = SqliteSchemaConnectionFactory(
        root,
        load_workspace_migrations(with_runtime=True),
    )
    return PublishObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(schema),
    )(
        f"digest-payload:{suffix}".encode(),
        "digest",
        "application/json",
        "retain",
    )


def _seed_subscription(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            SUBSCRIPTION,
            READER,
            "email",
            1,
            "Asia/Taipei",
            '{"kind":"daily","local_time":"08:00"}',
            10,
            "recipient:restore",
            1,
            NOW.isoformat(),
        ),
    )


def _seed_outbox(
    root: Path,
    connection: sqlite3.Connection,
    *,
    suffix: str,
    workspace_epoch: int,
    state: str,
) -> tuple[str, str, StoredDigestPayload]:
    ref = _publish_digest(root, suffix)
    work_id = f"work:{suffix}"
    event_id = f"event:{suffix}"
    digest_id = f"digest-row:{suffix}"
    outbox_id = f"outbox:{suffix}"
    period_key = f"2026-09-{suffix}"
    connection.execute(
        "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
        (
            work_id,
            f"Paper {suffix}",
            "published",
            "2026-09-26",
            "day",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
        (
            event_id,
            work_id,
            None,
            "correction",
            f"event-key:{suffix}",
            "{}",
            NOW.isoformat(),
            NOW.isoformat(),
        ),
    )
    digest_state = {
        "pending": "queued",
        "sending": "queued",
        "failed": "queued",
        "unknown": "unknown",
        "provider_accepted": "sent",
        "cancelled": "cancelled",
    }[state]
    ledger_state = {
        "pending": "reserved",
        "sending": "reserved",
        "failed": "reserved",
        "unknown": "unknown",
        "provider_accepted": "accepted",
        "cancelled": "cancelled",
    }[state]
    connection.execute(
        "INSERT INTO digests VALUES(?,?,?,?,?,?,?)",
        (
            digest_id,
            SUBSCRIPTION,
            period_key,
            NOW.isoformat(),
            ref.object_id,
            digest_state,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO digest_items VALUES(?,?,?,?,?,?,?)",
        (
            digest_id,
            1,
            event_id,
            work_id,
            None,
            None,
            "status_notice",
        ),
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES(?,?,?,?,?,?,?,?)",
        (
            outbox_id,
            digest_id,
            f"idempotency:{suffix}",
            ref.content_sha256,
            state,
            workspace_epoch,
            None,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO notification_ledger VALUES(?,?,?,?,?,?,?)",
        (
            f"ledger:{suffix}",
            READER,
            event_id,
            "email",
            outbox_id,
            ledger_state,
            NOW.isoformat(),
        ),
    )
    if state in {"sending", "failed", "unknown", "provider_accepted"}:
        connection.execute(
            "INSERT INTO delivery_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                f"attempt:{suffix}",
                outbox_id,
                1,
                state,
                f"provider:{suffix}" if state == "provider_accepted" else None,
                "fixture" if state in {"failed", "unknown"} else None,
                NOW.isoformat(),
                None if state == "sending" else NOW.isoformat(),
            ),
        )
    payload = StoredDigestPayload(
        SUBSCRIPTION,
        period_key,
        f"fingerprint:{suffix}",
        f"Subject {suffix}",
        f"Text {suffix}",
        f"<p>{suffix}</p>",
    )
    return outbox_id, ref.object_id, payload


def _backup_and_restore(
    root: Path,
    migrations,
    target: Path,
):
    backup = BackupWorkspace(LocalWorkspaceBackupAdapter(root))(
        run_id="backup:effects",
        created_at=NOW,
    )
    return RestoreWorkspace(LocalWorkspaceRestoreAdapter(migrations))(
        backup_directory=root / backup.relative_directory,
        target=target,
    )


def test_restore_bumps_epoch_disables_effects_and_lists_reconciliation(
    tmp_path: Path,
) -> None:
    root, migrations, schema = _workspace(tmp_path)
    SetWorkspaceExternalEffects(
        SqliteWorkspaceExternalEffectsAdapter(schema.connect)
    )(True)
    connection = schema.connect()
    try:
        _seed_subscription(connection)
        for state in (
            "pending",
            "sending",
            "failed",
            "unknown",
            "provider_accepted",
            "cancelled",
        ):
            _seed_outbox(
                root,
                connection,
                suffix=state,
                workspace_epoch=1,
                state=state,
            )
        connection.commit()
    finally:
        connection.close()

    target = tmp_path / "restored"
    result = _backup_and_restore(root, migrations, target)

    assert result.previous_epoch == 1
    assert result.epoch == 2
    assert result.reconciliation_outbox_ids == (
        "outbox:failed",
        "outbox:pending",
        "outbox:sending",
        "outbox:unknown",
    )

    restored = SqliteSchemaConnectionFactory(target, migrations)
    connection = restored.connect()
    try:
        metadata = connection.execute(
            "SELECT epoch,external_effects_enabled "
            "FROM workspace_metadata WHERE singleton=1"
        ).fetchone()
        assert tuple(metadata) == (2, 0)
        states = connection.execute(
            "SELECT id,state,workspace_epoch FROM delivery_outbox ORDER BY id"
        ).fetchall()
        assert {(row["id"], row["state"], row["workspace_epoch"]) for row in states} == {
            ("outbox:cancelled", "cancelled", 1),
            ("outbox:failed", "failed", 1),
            ("outbox:pending", "pending", 1),
            ("outbox:provider_accepted", "provider_accepted", 1),
            ("outbox:sending", "sending", 1),
            ("outbox:unknown", "unknown", 1),
        }
    finally:
        connection.close()


def test_old_epoch_outbox_cannot_send_after_restore_or_reenable(
    tmp_path: Path,
) -> None:
    root, migrations, schema = _workspace(tmp_path)
    SetWorkspaceExternalEffects(
        SqliteWorkspaceExternalEffectsAdapter(schema.connect)
    )(True)
    reader = FakeDigestReader()
    connection = schema.connect()
    try:
        _seed_subscription(connection)
        old_outbox, old_object, old_payload = _seed_outbox(
            root,
            connection,
            suffix="old",
            workspace_epoch=1,
            state="pending",
        )
        reader.payloads[old_object] = old_payload
        connection.commit()
    finally:
        connection.close()

    target = tmp_path / "restored"
    result = _backup_and_restore(root, migrations, target)
    restored = SqliteSchemaConnectionFactory(target, migrations)
    sender = FakeSender()
    dispatch = DispatchDigest(
        store=SqliteDeliveryStoreAdapter(restored.connect),
        artifacts=reader,
        recipients=FakeRecipientResolver(),
        sender=sender,
    )

    first = dispatch(old_outbox, now=NOW)
    assert first.state == "reconciliation_required"
    assert sender.messages == []

    SetWorkspaceExternalEffects(
        SqliteWorkspaceExternalEffectsAdapter(restored.connect)
    )(True)
    second = dispatch(old_outbox, now=NOW)
    assert second.state == "reconciliation_required"
    assert sender.messages == []

    connection = restored.connect()
    try:
        attempts = connection.execute(
            "SELECT count(*) FROM delivery_attempts WHERE outbox_id=?",
            (old_outbox,),
        ).fetchone()[0]
        assert attempts == 0
        new_outbox, new_object, new_payload = _seed_outbox(
            target,
            connection,
            suffix="new",
            workspace_epoch=result.epoch,
            state="pending",
        )
        reader.payloads[new_object] = new_payload
        connection.commit()
    finally:
        connection.close()

    third = dispatch(new_outbox, now=NOW)
    assert third.state == "provider_accepted"
    assert len(sender.messages) == 1
