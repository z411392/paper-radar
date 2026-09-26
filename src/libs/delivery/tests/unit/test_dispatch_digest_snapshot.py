from datetime import datetime, timezone
from types import SimpleNamespace

from libs.delivery.application.commands.dispatch_digest import DispatchDigest
from libs.delivery.dtos.delivery_dispatch import (
    DeliveryClaim,
    DeliveryDispatchCandidate,
)


NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


class Store:
    def __init__(self) -> None:
        self.claim = None

    def load_dispatch(self, outbox_id):
        assert outbox_id == "outbox:test"
        return DeliveryDispatchCandidate(
            "outbox:test",
            "digest:test",
            "subscription:test",
            "2026-09-27",
            "digest:" + "a" * 64,
            "delivery-request:" + "b" * 64,
            "a" * 64,
            7,
            "pending",
            "queued",
            "reader:test",
            "email",
            True,
            "recipient:test",
        )

    def claim_dispatch(
        self,
        outbox_id,
        now,
        *,
        expected_rendered_object_id,
        expected_payload_sha256,
        expected_idempotency_key,
    ):
        self.claim = (
            outbox_id,
            now,
            expected_rendered_object_id,
            expected_payload_sha256,
            expected_idempotency_key,
        )
        return DeliveryClaim("snapshot_changed")

    def finish_dispatch(self, *args, **kwargs):
        raise AssertionError("finish must not run")


class Artifacts:
    def read(self, object_id):
        assert object_id == "digest:" + "a" * 64
        return SimpleNamespace(
            subscription_id="subscription:test",
            period_key="2026-09-27",
            subject="subject",
            text_body="text",
            html_body="<p>html</p>",
        )


class Recipients:
    def resolve(self, recipient_ref):
        assert recipient_ref == "recipient:test"
        return "reader@example.invalid"


class Sender:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, message):
        self.calls += 1
        raise AssertionError("stale snapshot must not send")


def test_dispatch_binds_claim_to_loaded_payload_snapshot() -> None:
    store = Store()
    sender = Sender()
    command = DispatchDigest(
        store=store,
        artifacts=Artifacts(),
        recipients=Recipients(),
        sender=sender,
    )

    outcome = command("outbox:test", now=NOW)

    assert outcome.state == "snapshot_changed"
    assert sender.calls == 0
    assert store.claim == (
        "outbox:test",
        NOW,
        "digest:" + "a" * 64,
        "a" * 64,
        "delivery-request:" + "b" * 64,
    )
