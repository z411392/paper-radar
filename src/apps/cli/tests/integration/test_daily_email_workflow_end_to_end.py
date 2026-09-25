from datetime import datetime, timedelta, timezone
from pathlib import Path

from injector import Injector

import apps.cli.module as cli_module
from apps.cli.module import WorkerCliModule
from libs.delivery.adapters.driven.sqlite_delivery_subscription_store_adapter import (
    SqliteDeliverySubscriptionStoreAdapter,
)
from libs.delivery.application.commands.configure_delivery_subscription import (
    ConfigureDeliverySubscription,
)
from libs.delivery.dtos.delivery_dispatch import MailSendResult
from libs.delivery.dtos.delivery_subscription import (
    ConfigureDeliverySubscriptionRequest,
)
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
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
from libs.kernel.application.commands.set_workspace_external_effects import (
    SetWorkspaceExternalEffects,
)
from libs.research_workflow.ports.run_worker_cycle_port import RunWorkerCyclePort


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
WORK = "work:mail-e2e"
PRIOR_EVENT = "event:prior"
NOTICE_EVENT = "event:notice"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class FakeRecipients:
    def resolve(self, recipient_ref: str) -> str | None:
        assert recipient_ref == "recipient:primary"
        return "reader@example.com"


class FakeSender:
    def __init__(self, state: str) -> None:
        self.state = state
        self.calls = 0
        self.messages = []

    def send(self, message):
        self.calls += 1
        self.messages.append(message)
        if self.state == "provider_accepted":
            return MailSendResult(
                "provider_accepted",
                "provider:mail-e2e",
                None,
            )
        if self.state == "unknown":
            return MailSendResult("unknown", None, "source_timeout")
        raise AssertionError("unsupported fake state")


def _bootstrap(root: Path):
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.external_effects_enabled is False
    schema = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=len(migrations),
    )
    configure = ConfigureDeliverySubscription(
        SqliteDeliverySubscriptionStoreAdapter(schema.connect),
        clock=lambda: NOW,
    )
    subscription = configure(
        ConfigureDeliverySubscriptionRequest(
            reader_id="reader:local",
            timezone="Asia/Taipei",
            local_time="08:00",
            max_items=5,
            recipient_ref="recipient:primary",
            enabled=True,
        )
    )
    return info, schema, subscription


def _seed_prior_recipient_and_notice(
    schema: SqliteSchemaConnectionFactory,
    subscription_id: str,
) -> None:
    connection = schema.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "Paper with a correction",
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
                PRIOR_EVENT,
                WORK,
                None,
                "correction",
                "event-key:prior",
                '{"provider":"fixture"}',
                None,
                (NOW - timedelta(days=2)).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
            (
                NOTICE_EVENT,
                WORK,
                None,
                "correction",
                "event-key:notice",
                '{"provider":"fixture"}',
                None,
                "2026-09-25T00:00:00+00:00",
            ),
        )
        digest_object_id = "digest:" + "f" * 64
        connection.execute(
            "INSERT INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
            (
                digest_object_id,
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
                subscription_id,
                "prior-period",
                (NOW - timedelta(days=2)).isoformat(),
                digest_object_id,
                (NOW - timedelta(days=2)).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO delivery_outbox VALUES(?,?,?,?, 'provider_accepted',1,?)",
            (
                "outbox:prior",
                "digest:prior",
                "request:prior",
                "f" * 64,
                (NOW - timedelta(days=2)).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO notification_ledger VALUES(?,?,?,?,?,'accepted',?)",
            (
                "ledger:prior",
                "reader:local",
                PRIOR_EVENT,
                "email",
                "outbox:prior",
                (NOW - timedelta(days=2)).isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _cycle(root: Path, monkeypatch, sender: FakeSender):
    clock = MutableClock(NOW)
    monkeypatch.setattr(
        cli_module,
        "SystemWorkflowClockAdapter",
        lambda: clock,
    )
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
    return clock, injector.get(RunWorkerCyclePort)


def _run(cycle, owner: str):
    return cycle(
        owner,
        max_new_jobs=20,
        max_jobs=20,
        lease_seconds=300,
    )


def _delivery_state(schema: SqliteSchemaConnectionFactory):
    connection = schema.connect()
    try:
        outbox = connection.execute(
            "SELECT id,state FROM delivery_outbox "
            "WHERE id<>'outbox:prior' ORDER BY created_at,id"
        ).fetchall()
        attempts = connection.execute(
            "SELECT state FROM delivery_attempts ORDER BY started_at,id"
        ).fetchall()
        jobs = connection.execute(
            "SELECT job_kind,state,error_code FROM workflow_jobs "
            "ORDER BY created_at,id"
        ).fetchall()
        ledger = connection.execute(
            "SELECT event_id,state FROM notification_ledger "
            "WHERE event_id=?",
            (NOTICE_EVENT,),
        ).fetchall()
        return (
            [tuple(row) for row in outbox],
            [row[0] for row in attempts],
            [tuple(row) for row in jobs],
            [tuple(row) for row in ledger],
        )
    finally:
        connection.close()


def test_formal_workflow_waits_for_effects_then_sends_exactly_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "runtime"
    info, schema, subscription = _bootstrap(root)
    _seed_prior_recipient_and_notice(schema, subscription.subscription_id)
    sender = FakeSender("provider_accepted")
    clock, cycle = _cycle(root, monkeypatch, sender)

    prepared = _run(cycle, "worker:prepare")
    assert any(job.job_kind == "prepare_digest" for job in prepared.jobs)
    assert sender.calls == 0

    blocked = _run(cycle, "worker:dispatch-off")
    assert any(
        job.job_kind == "dispatch_digest"
        and job.state == "awaiting_external"
        and job.error_code == "delivery_effects_disabled"
        for job in blocked.jobs
    )
    assert sender.calls == 0
    outbox, attempts, _, ledger = _delivery_state(schema)
    assert len(outbox) == 1 and outbox[0][1] == "pending"
    assert attempts == []
    assert ledger == [(NOTICE_EVENT, "reserved")]

    enabled = SetWorkspaceExternalEffects(
        SqliteWorkspaceExternalEffectsAdapter(schema.connect)
    )(True)
    assert enabled.workspace_id == info.workspace_id
    assert enabled.epoch == info.epoch
    assert enabled.external_effects_enabled is True

    clock.advance(timedelta(hours=1))
    sent = _run(cycle, "worker:dispatch-on")
    assert any(
        job.job_kind == "dispatch_digest" and job.state == "succeeded"
        for job in sent.jobs
    )
    assert sender.calls == 1
    assert sender.messages[0].recipient == "reader@example.com"

    outbox, attempts, _, ledger = _delivery_state(schema)
    assert outbox[0][1] == "provider_accepted"
    assert attempts == ["provider_accepted"]
    assert ledger == [(NOTICE_EVENT, "accepted")]

    replay = _run(cycle, "worker:replay")
    assert replay.processed_jobs == 0
    assert sender.calls == 1


def test_unknown_delivery_is_never_automatically_resent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "runtime"
    _, schema, subscription = _bootstrap(root)
    _seed_prior_recipient_and_notice(schema, subscription.subscription_id)
    sender = FakeSender("unknown")
    clock, cycle = _cycle(root, monkeypatch, sender)

    _run(cycle, "worker:prepare")
    SetWorkspaceExternalEffects(
        SqliteWorkspaceExternalEffectsAdapter(schema.connect)
    )(True)

    dispatched = _run(cycle, "worker:unknown")
    assert any(
        job.job_kind == "dispatch_digest"
        and job.state == "awaiting_external"
        and job.error_code == "delivery_unknown_no_provider_lookup"
        for job in dispatched.jobs
    )
    assert sender.calls == 1

    outbox, attempts, _, ledger = _delivery_state(schema)
    assert outbox[0][1] == "unknown"
    assert attempts == ["unknown"]
    assert ledger == [(NOTICE_EVENT, "unknown")]

    clock.advance(timedelta(hours=1))
    replay = _run(cycle, "worker:unknown-recheck")
    assert any(
        job.job_kind == "dispatch_digest"
        and job.state == "awaiting_external"
        for job in replay.jobs
    )
    assert sender.calls == 1
    outbox, attempts, _, ledger = _delivery_state(schema)
    assert outbox[0][1] == "unknown"
    assert attempts == ["unknown"]
    assert ledger == [(NOTICE_EVENT, "unknown")]
