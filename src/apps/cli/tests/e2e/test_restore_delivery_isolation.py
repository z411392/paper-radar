import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
from libs.kernel.adapters.driven.sqlite_connection_factory import (
    SqliteConnectionFactory,
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
from libs.research_workflow.application.commands.process_revision_notice import (
    ProcessRevisionNotice,
)
from libs.research_workflow.application.commands.restore_workspace import RestoreWorkspace
from libs.research_workflow.dtos.revision_notice import RevisionNoticeRequest


NOW = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
OUTBOX = "outbox:restore-old"


class FakeArtifacts:
    def __init__(self, object_id: str, payload_sha256: str) -> None:
        self._object_id = object_id
        self._payload_sha256 = payload_sha256

    def read(self, object_id: str) -> StoredDigestPayload:
        assert object_id == self._object_id
        assert object_id == "digest:" + self._payload_sha256
        return StoredDigestPayload(
            subscription_id="subscription:restore",
            period_key="2026-09-26",
            content_fingerprint="a" * 64,
            subject="subject",
            text_body="text",
            html_body="<p>html</p>",
        )


class FakeRecipients:
    def resolve(self, recipient_ref: str) -> str | None:
        assert recipient_ref == "recipient:restore"
        return "reader@example.com"


class FakeSender:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, message) -> MailSendResult:
        self.calls += 1
        return MailSendResult("provider_accepted", "provider:restore", None)


def _schema(root: Path):
    migrations = load_workspace_migrations(with_runtime=True)
    return migrations, SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=len(migrations),
    )


def _publish_digest(root: Path):
    return PublishObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(SqliteConnectionFactory(root)),
    )(
        b"restore-isolation-digest",
        "digest",
        "application/json",
        "daily-digest-v1",
    )


def _seed_pending_delivery(root: Path) -> tuple[int, str, str]:
    migrations, schema = _schema(root)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    digest_ref = _publish_digest(root)
    connection = schema.connect()
    try:
        connection.execute(
            "INSERT INTO paper_works("
            "id,canonical_title,publication_status,first_seen_at,created_at"
            ") VALUES(?,?,?, ?,?)",
            (
                "work:restore",
                "Restore fixture",
                "published",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO research_events("
            "id,work_id,revision_id,event_kind,canonical_event_key,"
            "source_evidence_json,occurred_at,observed_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (
                "event:restore",
                "work:restore",
                None,
                "correction",
                "restore:event",
                "{}",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO delivery_subscriptions("
            "id,reader_id,channel,enabled,timezone,schedule_json,max_items,"
            "recipient_ref,policy_version,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "subscription:restore",
                "reader:restore",
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
        connection.execute(
            "INSERT INTO digests("
            "id,subscription_id,period_key,cutoff_at,rendered_object_id,state,created_at"
            ") VALUES(?,?,?,?,?,'queued',?)",
            (
                "digest:restore-row",
                "subscription:restore",
                "2026-09-26",
                NOW.isoformat(),
                digest_ref.object_id,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO digest_items("
            "digest_id,position,event_id,work_id,summary_id,revision_id,item_kind"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                "digest:restore-row",
                1,
                "event:restore",
                "work:restore",
                None,
                None,
                "status_notice",
            ),
        )
        connection.execute(
            "INSERT INTO delivery_outbox("
            "id,digest_id,idempotency_key,payload_sha256,state,workspace_epoch,"
            "next_attempt_at,created_at"
            ") VALUES(?,?,?,?, 'pending',?,NULL,?)",
            (
                OUTBOX,
                "digest:restore-row",
                "delivery-request:restore",
                digest_ref.content_sha256,
                info.epoch,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO notification_ledger("
            "id,reader_id,event_id,channel,outbox_id,state,created_at"
            ") VALUES(?,?,?,?,?,'reserved',?)",
            (
                "ledger:restore",
                "reader:restore",
                "event:restore",
                "email",
                OUTBOX,
                NOW.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    effects = SetWorkspaceExternalEffects(
        SqliteWorkspaceExternalEffectsAdapter(schema.connect)
    )(True)
    assert effects.external_effects_enabled is True
    return info.epoch, digest_ref.object_id, digest_ref.content_sha256


def _dispatch(
    root: Path,
    object_id: str,
    payload_sha256: str,
    sender: FakeSender,
):
    _, schema = _schema(root)
    return DispatchDigest(
        store=SqliteDeliveryStoreAdapter(schema.connect),
        artifacts=FakeArtifacts(object_id, payload_sha256),
        recipients=FakeRecipients(),
        sender=sender,
    )


def _set_delivery_state(root: Path, state: str) -> None:
    mapping = {
        "pending": ("queued", "reserved", None),
        "sending": ("queued", "reserved", "sending"),
        "failed": ("queued", "reserved", "failed"),
        "unknown": ("unknown", "unknown", "unknown"),
        "provider_accepted": ("sent", "accepted", "provider_accepted"),
        "cancelled": ("cancelled", "cancelled", None),
    }
    digest_state, ledger_state, attempt_state = mapping[state]
    _, schema = _schema(root)
    connection = schema.connect()
    try:
        connection.execute(
            "UPDATE delivery_outbox SET state=? WHERE id=?",
            (state, OUTBOX),
        )
        connection.execute(
            "UPDATE digests SET state=? WHERE id='digest:restore-row'",
            (digest_state,),
        )
        connection.execute(
            "UPDATE notification_ledger SET state=? WHERE outbox_id=?",
            (ledger_state, OUTBOX),
        )
        if attempt_state is not None:
            connection.execute(
                "INSERT INTO delivery_attempts("
                "id,outbox_id,attempt_no,state,provider_message_id,error_code,"
                "started_at,finished_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"attempt:{state}",
                    OUTBOX,
                    1,
                    attempt_state,
                    "provider:historical"
                    if attempt_state == "provider_accepted"
                    else None,
                    "fixture"
                    if attempt_state in {"failed", "unknown"}
                    else None,
                    NOW.isoformat(),
                    None if attempt_state == "sending" else NOW.isoformat(),
                ),
            )
        connection.commit()
    finally:
        connection.close()


class PriorRecipientTrue:
    def contains(self, reader_id: str, channel: str, work_id: str) -> bool:
        assert reader_id == "reader:restore"
        assert channel == "email"
        assert work_id == "work:restore"
        return True


class SummariesUnused:
    def __call__(self, work_id: str, revision_id: str):
        raise AssertionError("status notice must not request a summary")


def _outbox_state(root: Path) -> tuple[str, int]:
    _, schema = _schema(root)
    connection = schema.connect()
    try:
        row = connection.execute(
            "SELECT state,workspace_epoch FROM delivery_outbox WHERE id=?",
            (OUTBOX,),
        ).fetchone()
        assert row is not None
        return row["state"], row["workspace_epoch"]
    finally:
        connection.close()


def test_old_backup_outbox_cannot_resend_even_after_effects_are_reenabled(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    old_epoch, object_id, payload_sha256 = _seed_pending_delivery(source)

    backup = BackupWorkspace(LocalWorkspaceBackupAdapter(source))(
        run_id="backup:before-send",
        created_at=NOW,
    )

    live_sender = FakeSender()
    live = _dispatch(source, object_id, payload_sha256, live_sender)
    accepted = live(OUTBOX, now=NOW)
    assert accepted.state == "provider_accepted"
    assert live_sender.calls == 1
    assert _outbox_state(source)[0] == "provider_accepted"

    restored = tmp_path / "restored"
    migrations = load_workspace_migrations(with_runtime=True)
    result = RestoreWorkspace(LocalWorkspaceRestoreAdapter(migrations))(
        backup_directory=source / backup.relative_directory,
        target=restored,
    )

    assert result.previous_epoch == old_epoch
    assert result.epoch == old_epoch + 1
    assert result.external_effects_enabled is False
    assert result.source_backup_run_id == "backup:before-send"
    assert result.reconciliation_outbox_ids == (OUTBOX,)
    assert _outbox_state(restored) == ("pending", old_epoch)

    restored_sender = FakeSender()
    restored_dispatch = _dispatch(
        restored,
        object_id,
        payload_sha256,
        restored_sender,
    )
    blocked = restored_dispatch(OUTBOX, now=NOW)
    assert blocked.state == "reconciliation_required"
    assert restored_sender.calls == 0

    _, restored_schema = _schema(restored)
    enabled = SetWorkspaceExternalEffects(
        SqliteWorkspaceExternalEffectsAdapter(restored_schema.connect)
    )(True)
    assert enabled.external_effects_enabled is True
    assert enabled.epoch == old_epoch + 1

    still_blocked = restored_dispatch(OUTBOX, now=NOW)
    assert still_blocked.state == "reconciliation_required"
    assert restored_sender.calls == 0
    assert _outbox_state(restored) == ("pending", old_epoch)


def test_restore_records_provenance_and_keeps_effects_off(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    old_epoch, _, _ = _seed_pending_delivery(source)
    backup = BackupWorkspace(LocalWorkspaceBackupAdapter(source))(
        run_id="backup:provenance",
        created_at=NOW,
    )
    target = tmp_path / "restored"
    result = RestoreWorkspace(
        LocalWorkspaceRestoreAdapter(load_workspace_migrations(with_runtime=True))
    )(
        backup_directory=source / backup.relative_directory,
        target=target,
    )

    _, schema = _schema(target)
    connection = schema.connect()
    try:
        row = connection.execute(
            "SELECT workspace_id,epoch,external_effects_enabled,restored_from "
            "FROM workspace_metadata WHERE singleton=1"
        ).fetchone()
        assert row is not None
        assert row["workspace_id"] == result.workspace_id
        assert row["epoch"] == old_epoch + 1
        assert row["external_effects_enabled"] == 0
        assert row["restored_from"] == "backup:provenance"
    finally:
        connection.close()



@pytest.mark.parametrize(
    ("state", "requires_reconciliation"),
    [
        ("pending", True),
        ("sending", True),
        ("failed", True),
        ("unknown", True),
        ("provider_accepted", False),
        ("cancelled", False),
    ],
)
def test_restore_reconciliation_set_is_derived_from_epoch_and_terminal_state(
    tmp_path: Path,
    state: str,
    requires_reconciliation: bool,
) -> None:
    source = tmp_path / f"source-{state}"
    old_epoch, _, _ = _seed_pending_delivery(source)
    _set_delivery_state(source, state)
    backup = BackupWorkspace(LocalWorkspaceBackupAdapter(source))(
        run_id=f"backup:{state}",
        created_at=NOW,
    )
    target = tmp_path / f"restored-{state}"

    result = RestoreWorkspace(
        LocalWorkspaceRestoreAdapter(load_workspace_migrations(with_runtime=True))
    )(
        backup_directory=source / backup.relative_directory,
        target=target,
    )

    assert result.epoch == old_epoch + 1
    assert (OUTBOX in result.reconciliation_outbox_ids) is requires_reconciliation
    restored_state, restored_outbox_epoch = _outbox_state(target)
    assert restored_state == state
    assert restored_outbox_epoch == old_epoch


def test_revision_notice_keeps_restored_old_outbox_awaiting_external(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-workflow"
    _, object_id, payload_sha256 = _seed_pending_delivery(source)
    backup = BackupWorkspace(LocalWorkspaceBackupAdapter(source))(
        run_id="backup:workflow",
        created_at=NOW,
    )
    target = tmp_path / "restored-workflow"
    RestoreWorkspace(
        LocalWorkspaceRestoreAdapter(load_workspace_migrations(with_runtime=True))
    )(
        backup_directory=source / backup.relative_directory,
        target=target,
    )

    sender = FakeSender()
    dispatch = _dispatch(target, object_id, payload_sha256, sender)
    _, schema = _schema(target)
    notice = ProcessRevisionNotice(
        store=SqliteDeliveryStoreAdapter(schema.connect),
        summaries=SummariesUnused(),
        prior_recipient=PriorRecipientTrue(),
        dispatch=dispatch,
        clock=lambda: NOW,
    )

    outcome = notice(RevisionNoticeRequest(OUTBOX, None))

    assert outcome.state == "awaiting_external"
    assert outcome.error_code == "delivery_restore_reconciliation_required"
    assert sender.calls == 0
    assert _outbox_state(target)[0] == "pending"
