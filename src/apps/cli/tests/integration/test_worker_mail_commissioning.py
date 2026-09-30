import hashlib
from datetime import datetime, timezone
from pathlib import Path

from injector import Injector

from apps.cli.module import WorkerCliModule
from libs.delivery.adapters.driven.kernel_digest_artifact_adapter import (
    KernelDigestArtifactAdapter,
)
from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import (
    SqliteDeliveryStoreAdapter,
)
from libs.delivery.application.commands.queue_digest import QueueDigest
from libs.delivery.dtos.delivery_dispatch import MailSendResult
from libs.delivery.dtos.digest_preview import DigestPreview, SelectedDigestItem
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
from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.research_workflow.dtos.revision_notice import RevisionNoticeRequest
from libs.research_workflow.ports.process_revision_notice_port import (
    ProcessRevisionNoticePort,
)


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
WORK = "work:mail-commissioning"


class FakeSender:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, message):
        self.calls += 1
        assert message.recipient == "reader@example.com"
        return MailSendResult("provider_accepted", "provider:test", None)


class FakeRecipients:
    def resolve(self, recipient_ref):
        assert recipient_ref == "recipient:primary"
        return "reader@example.com"


def _seed(root: Path):
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version >= 23
    assert info.external_effects_enabled is False
    schema = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=23,
    )
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
        for event_id in ("event:prior", "event:notice"):
            connection.execute(
                "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    WORK,
                    None,
                    "correction",
                    "key:" + event_id,
                    '{"provider":"fixture"}',
                    None,
                    NOW.isoformat(),
                ),
            )
        connection.execute(
            "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "subscription:daily",
                "reader:local",
                "email",
                1,
                "Asia/Taipei",
                '{"kind":"daily","local_time":"08:00"}',
                5,
                "recipient:primary",
                1,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "digest:" + "f" * 64,
                "f" * 64,
                "objects/digest/ff/" + "f" * 64,
                "digest",
                "application/json",
                1,
                "available",
                NOW.isoformat(),
                "delivery",
            ),
        )
        connection.execute(
            "INSERT INTO digests VALUES(?,?,?,?,?,'sent',?)",
            (
                "digest:prior",
                "subscription:daily",
                "prior",
                NOW.isoformat(),
                "digest:" + "f" * 64,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO delivery_outbox VALUES(?,?,?,?, 'provider_accepted',1,?)",
            (
                "outbox:prior",
                "digest:prior",
                "request:prior",
                "f" * 64,
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO notification_ledger VALUES(?,?,?,?,?,'accepted',?)",
            (
                "ledger:prior",
                "reader:local",
                "event:prior",
                "email",
                "outbox:prior",
                NOW.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    files = FilesystemObjectBytesAdapter(root)
    raw = SqliteConnectionFactory(root)
    objects = SqliteObjectUnitOfWorkAdapter(raw)
    artifacts = KernelDigestArtifactAdapter(
        PublishObject(files, objects),
        ReadObject(files, objects),
    )
    queue = QueueDigest(
        artifacts,
        SqliteDeliveryStoreAdapter(schema.connect),
    )
    item = SelectedDigestItem(
        event_id="event:notice",
        work_id=WORK,
        summary_id=None,
        revision_id=None,
        event_at=NOW,
        priority=100,
        domains=(),
        title="Status paper",
        source_url=None,
        plain_language=("來源目前回報這篇研究有更正紀錄。",),
        item_kind="status_notice",
        event_kind="correction",
    )
    marker = "mail commissioning status notice"
    preview = DigestPreview(
        "subscription:daily",
        "2026-09-25",
        NOW,
        True,
        (item,),
        "Paper Radar status",
        marker,
        "<p>" + marker + "</p>",
        hashlib.sha256(marker.encode()).hexdigest(),
    )
    queued = queue(
        preview,
        reader_id="reader:local",
        channel="email",
        workspace_epoch=info.epoch,
        created_at=NOW,
    )
    return schema, queued.outbox_id


def test_mail_sender_is_called_only_after_workspace_effects_are_enabled(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    schema, outbox_id = _seed(root)
    sender = FakeSender()
    injector = Injector(
        [
            WorkerCliModule(
                str(root),
                allow_live_mail=True,
                mail_sender=sender,
                recipient_resolver=FakeRecipients(),
            )
        ],
        auto_bind=False,
    )
    notice = injector.get(ProcessRevisionNoticePort)

    disabled = notice(RevisionNoticeRequest(outbox_id))
    assert (disabled.state, disabled.error_code) == (
        "awaiting_external",
        "delivery_effects_disabled",
    )
    assert sender.calls == 0

    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT state FROM delivery_outbox WHERE id=?",
            (outbox_id,),
        ).fetchone()[0] == "pending"
        assert connection.execute(
            "SELECT count(*) FROM delivery_attempts WHERE outbox_id=?",
            (outbox_id,),
        ).fetchone()[0] == 0
        connection.execute(
            "UPDATE workspace_metadata SET external_effects_enabled=1 "
            "WHERE singleton=1"
        )
        connection.commit()
    finally:
        connection.close()

    sent = notice(RevisionNoticeRequest(outbox_id))
    replay = notice(RevisionNoticeRequest(outbox_id))

    assert sent.state == "succeeded"
    assert replay.state == "succeeded"
    assert sender.calls == 1
    connection = schema.connect()
    try:
        assert connection.execute(
            "SELECT state FROM delivery_outbox WHERE id=?",
            (outbox_id,),
        ).fetchone()[0] == "provider_accepted"
        attempts = connection.execute(
            "SELECT state FROM delivery_attempts WHERE outbox_id=?",
            (outbox_id,),
        ).fetchall()
        assert [row[0] for row in attempts] == ["provider_accepted"]
    finally:
        connection.close()
