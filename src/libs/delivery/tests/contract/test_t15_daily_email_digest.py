import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import SqliteDeliveryStoreAdapter
from libs.delivery.application.commands.dispatch_digest import DispatchDigest
from libs.delivery.application.commands.reconcile_delivery import ReconcileDelivery
from libs.delivery.dtos.delivery_dispatch import MailSendResult
from libs.delivery.dtos.digest_artifact import StoredDigestPayload


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class FakeArtifacts:
    def __init__(self) -> None:
        self.payload = StoredDigestPayload(
            subscription_id="subscription:test",
            period_key="2026-09-23",
            content_fingerprint="a" * 64,
            subject="subject",
            text_body="text",
            html_body="<p>html</p>",
        )

    def read(self, object_id: str) -> StoredDigestPayload:
        assert object_id == "digest:" + "d" * 64
        return self.payload


class FakeRecipients:
    def __init__(self, recipient: str | None) -> None:
        self.recipient = recipient

    def resolve(self, recipient_ref: str) -> str | None:
        assert recipient_ref == "recipient:primary"
        return self.recipient


class FakeSender:
    def __init__(self, result: MailSendResult) -> None:
        self.result = result
        self.calls = 0

    def send(self, message):
        self.calls += 1
        assert message.recipient == "reader@example.com"
        assert message.subject == "subject"
        return self.result


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path, *, enabled: int = 1, effects: int = 1):
    path = tmp_path / "delivery.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE workspace_metadata(
          singleton INTEGER PRIMARY KEY,epoch INTEGER NOT NULL,external_effects_enabled INTEGER NOT NULL);
        CREATE TABLE delivery_subscriptions(
          id TEXT PRIMARY KEY,reader_id TEXT NOT NULL,channel TEXT NOT NULL,enabled INTEGER NOT NULL,
          recipient_ref TEXT NOT NULL);
        CREATE TABLE digests(
          id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL,period_key TEXT NOT NULL,cutoff_at TEXT NOT NULL,
          rendered_object_id TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE delivery_outbox(
          id TEXT PRIMARY KEY,digest_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
          state TEXT NOT NULL,workspace_epoch INTEGER NOT NULL,next_attempt_at TEXT,created_at TEXT NOT NULL);
        CREATE TABLE notification_ledger(
          id TEXT PRIMARY KEY,reader_id TEXT NOT NULL,event_id TEXT NOT NULL,channel TEXT NOT NULL,
          outbox_id TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE delivery_attempts(
          id TEXT PRIMARY KEY,outbox_id TEXT NOT NULL,attempt_no INTEGER NOT NULL,state TEXT NOT NULL,
          provider_message_id TEXT,error_code TEXT,started_at TEXT NOT NULL,finished_at TEXT,
          UNIQUE(outbox_id,attempt_no));
        """
    )
    connection.execute("INSERT INTO workspace_metadata VALUES(1,1,?)", (effects,))
    connection.execute(
        "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?)",
        ("subscription:test", "reader:test", "email", enabled, "recipient:primary"),
    )
    connection.execute(
        "INSERT INTO digests VALUES(?,?,?,?,?,'queued',?)",
        (
            "digest-record:test",
            "subscription:test",
            "2026-09-23",
            NOW.isoformat(),
            "digest:" + "d" * 64,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES(?,?,?,?, 'pending',1,NULL,?)",
        (
            "outbox:test",
            "digest-record:test",
            "delivery-request:test",
            "d" * 64,
            NOW.isoformat(),
        ),
    )
    connection.execute(
        "INSERT INTO notification_ledger VALUES(?,?,?,?,?,'reserved',?)",
        (
            "notification:test",
            "reader:test",
            "event:test",
            "email",
            "outbox:test",
            NOW.isoformat(),
        ),
    )
    connection.commit()
    connection.close()
    return path, SqliteDeliveryStoreAdapter(_connect(path))


def _states(path: Path):
    connection = sqlite3.connect(path)
    values = (
        connection.execute("SELECT state FROM digests").fetchone()[0],
        connection.execute("SELECT state FROM delivery_outbox").fetchone()[0],
        connection.execute("SELECT state FROM notification_ledger").fetchone()[0],
        tuple(connection.execute("SELECT state,error_code FROM delivery_attempts ORDER BY attempt_no")),
    )
    connection.close()
    return values


def _dispatch(store, recipient, sender):
    return DispatchDigest(
        store=store,
        artifacts=FakeArtifacts(),
        recipients=FakeRecipients(recipient),
        sender=sender,
    )


def test_missing_explicit_recipient_does_not_claim_or_send(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    sender = FakeSender(MailSendResult("provider_accepted", None, None))

    result = _dispatch(store, None, sender)("outbox:test", now=NOW)

    assert result.state == "recipient_missing"
    assert sender.calls == 0
    assert _states(path) == ("queued", "pending", "reserved", ())


def test_disabled_subscription_is_cancelled_before_external_effect(tmp_path: Path) -> None:
    path, store = _setup(tmp_path, enabled=0)
    sender = FakeSender(MailSendResult("provider_accepted", None, None))

    result = _dispatch(store, "reader@example.com", sender)("outbox:test", now=NOW)

    assert result.state == "cancelled"
    assert sender.calls == 0
    assert _states(path) == ("cancelled", "cancelled", "cancelled", ())


def test_external_effects_disabled_keeps_request_pending(tmp_path: Path) -> None:
    path, store = _setup(tmp_path, effects=0)
    sender = FakeSender(MailSendResult("provider_accepted", None, None))

    result = _dispatch(store, "reader@example.com", sender)("outbox:test", now=NOW)

    assert result.state == "effects_disabled"
    assert sender.calls == 0
    assert _states(path) == ("queued", "pending", "reserved", ())


def test_provider_acceptance_marks_request_accepted_but_not_read(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    sender = FakeSender(MailSendResult("provider_accepted", "provider:1", None))

    result = _dispatch(store, "reader@example.com", sender)("outbox:test", now=NOW)

    assert result.state == "provider_accepted"
    assert sender.calls == 1
    digest, outbox, ledger, attempts = _states(path)
    assert (digest, outbox, ledger) == ("sent", "provider_accepted", "accepted")
    assert attempts == (("provider_accepted", None),)


def test_timeout_unknown_is_terminal_for_automatic_dispatch_and_requires_manual_reconcile(
    tmp_path: Path,
) -> None:
    path, store = _setup(tmp_path)
    sender = FakeSender(MailSendResult("unknown", None, "smtp_timeout"))
    command = _dispatch(store, "reader@example.com", sender)

    first = command("outbox:test", now=NOW)
    second = command("outbox:test", now=NOW)
    reconciliation = ReconcileDelivery(store)("outbox:test")

    assert first.state == "unknown"
    assert second.state == "unknown"
    assert sender.calls == 1
    assert reconciliation.state == "manual_action_required"
    assert reconciliation.reason == "delivery_unknown_no_provider_lookup"
    assert _states(path) == (
        "unknown",
        "unknown",
        "unknown",
        (("unknown", "smtp_timeout"),),
    )


def test_explicit_rejection_is_failed_not_unknown(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)
    sender = FakeSender(MailSendResult("rejected", None, "recipient_rejected"))

    result = _dispatch(store, "reader@example.com", sender)("outbox:test", now=NOW)

    assert result.state == "failed"
    assert sender.calls == 1
    digest, outbox, ledger, attempts = _states(path)
    assert (digest, outbox, ledger) == ("queued", "failed", "reserved")
    assert attempts == (("failed", "recipient_rejected"),)


def test_unexpected_sender_exception_is_unknown_not_automatic_retry(tmp_path: Path) -> None:
    path, store = _setup(tmp_path)

    class ExplodingSender:
        calls = 0

        def send(self, message):
            self.calls += 1
            raise RuntimeError("transport vanished")

    sender = ExplodingSender()
    command = _dispatch(store, "reader@example.com", sender)

    first = command("outbox:test", now=NOW)
    second = command("outbox:test", now=NOW)

    assert first.state == "unknown"
    assert second.state == "unknown"
    assert sender.calls == 1
    assert ReconcileDelivery(store)("outbox:test").reason == "delivery_unknown_no_provider_lookup"
