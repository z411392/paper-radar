from contextlib import contextmanager
from datetime import timedelta

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.application.commands.attach_claimed_crossref_capture import (
    AttachClaimedCrossrefCapture,
)
from libs.discovery.exceptions.crossref_attachment_error import (
    CrossrefAttachmentError,
)
from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
from libs.discovery.tests.integration.test_crossref_claimed_attachment import (
    Fixture,
    NOW,
)


@pytest.fixture
def f(tmp_path):
    return Fixture(tmp_path / "runtime")


def test_normal_attach_still_rejects_expired_claim(f):
    f.stage()

    with pytest.raises(CrossrefAttachmentError, match="lease_invalid"):
        f.store.attach(
            f.claim,
            f.stored,
            f.decision,
            attached_at=NOW + timedelta(seconds=61),
        )

    assert f.sql("SELECT * FROM crossref_capture_recoveries") == []
    assert f.sql("SELECT * FROM crossref_harvest_page_attempts") == []


def test_expired_durable_inbox_can_recover_locally(f):
    f.stage()
    recovered_at = NOW + timedelta(seconds=61)

    result = f.store.recover(
        f.claim,
        f.stored,
        f.decision,
        recovered_at=recovered_at,
    )

    assert result.replayed is False
    assert result.receipt_id == f.stored.receipt_id
    row = f.sql("SELECT * FROM crossref_capture_recoveries")[0]
    assert row["claim_id"] == f.claim.claim_id
    assert row["attempt_id"] == result.attempt_id
    assert row["receipt_id"] == f.stored.receipt_id
    assert row["reason"] == "durable_inbox_after_dispatch"
    assert row["policy_version"] == "crossref-recovery-v1"
    page = f.sql("SELECT state,successful_receipt_id FROM crossref_harvest_pages")[0]
    assert tuple(page) == ("captured", f.stored.receipt_id)


def test_recovery_inside_original_lease_uses_normal_authority(f):
    f.stage()

    result = f.store.recover(
        f.claim,
        f.stored,
        f.decision,
        recovered_at=NOW + timedelta(seconds=1),
    )

    assert result.replayed is False
    assert f.sql("SELECT * FROM crossref_capture_recoveries") == []


def test_recovered_attachment_replays_readonly_after_more_time(f):
    f.stage()
    first = f.store.recover(
        f.claim,
        f.stored,
        f.decision,
        recovered_at=NOW + timedelta(seconds=61),
    )
    before = [
        tuple(tuple(row) for row in f.sql(f"SELECT * FROM {table}"))
        for table in (
            "crossref_harvest_page_attempts",
            "crossref_harvest_pages",
            "crossref_capture_recoveries",
        )
    ]

    replay = f.store.attach(
        f.claim,
        f.stored,
        f.decision,
        attached_at=NOW + timedelta(days=1),
    )

    assert replay.replayed is True
    assert replay.attempt_id == first.attempt_id
    after = [
        tuple(tuple(row) for row in f.sql(f"SELECT * FROM {table}"))
        for table in (
            "crossref_harvest_page_attempts",
            "crossref_harvest_pages",
            "crossref_capture_recoveries",
        )
    ]
    assert after == before


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE workspace_metadata SET epoch=epoch+1",
        "UPDATE crossref_harvest_passes SET state='failed'",
        "UPDATE crossref_harvest_passes SET current_cursor='later'",
        "UPDATE crossref_harvest_pages SET request_fingerprint='wrong'",
    ],
)
def test_recovery_rechecks_current_authority(f, mutation):
    f.stage()
    f.sql(mutation)

    with pytest.raises(CrossrefAttachmentError):
        f.store.recover(
            f.claim,
            f.stored,
            f.decision,
            recovered_at=NOW + timedelta(seconds=61),
        )

    assert f.sql("SELECT * FROM crossref_capture_recoveries") == []
    assert f.sql("SELECT * FROM crossref_harvest_page_attempts") == []


def test_recovery_receipt_failure_rolls_back_attempt_and_page(f):
    f.stage()
    f.sql(
        "CREATE TRIGGER injected AFTER INSERT ON crossref_capture_recoveries "
        "BEGIN SELECT RAISE(ABORT,'injected'); END"
    )

    with pytest.raises(CrossrefAttachmentError):
        f.store.recover(
            f.claim,
            f.stored,
            f.decision,
            recovered_at=NOW + timedelta(seconds=61),
        )

    assert f.sql("SELECT * FROM crossref_capture_recoveries") == []
    assert f.sql("SELECT * FROM crossref_harvest_page_attempts") == []
    page = f.sql(
        "SELECT state,successful_receipt_id,last_error_code "
        "FROM crossref_harvest_pages"
    )[0]
    assert tuple(page) == ("requested", None, None)


class ObservationOnlyGate:
    def __init__(self, now):
        self.now = now
        self.calls = 0

    @contextmanager
    def slot(self, contact_email):
        del contact_email
        raise AssertionError("local recovery must not acquire a send slot")
        yield  # pragma: no cover

    def observe_received(
        self,
        contact_email,
        status,
        headers,
        *,
        capture_error=None,
    ):
        assert contact_email == "fixture@example.invalid"
        self.calls += 1
        return CrossrefRatePolicy().evaluate(
            status,
            headers,
            now=self.now,
            capture_error=capture_error,
        )


@pytest.mark.parametrize(
    ("status", "headers", "action", "failure_code"),
    [
        (403, (), "stop", "crossref_forbidden"),
        (
            429,
            (("retry-after", "3600"),),
            "retry",
            "crossref_rate_limited",
        ),
    ],
)
def test_local_recovery_observes_received_response_without_send_slot(
    f,
    status,
    headers,
    action,
    failure_code,
):
    f.stage(status=status, headers=headers)
    recovered_at = NOW + timedelta(seconds=61)
    gate = ObservationOnlyGate(recovered_at)
    command = AttachClaimedCrossrefCapture(
        f.publisher,
        gate,
        f.store,
        source=CrossrefSourceAdapter(),
        clock=lambda: recovered_at,
    )

    result = command(f.plan, f.claim)

    assert gate.calls == 1
    assert result.action == action
    assert result.failure_code == failure_code
    assert len(f.sql("SELECT * FROM crossref_capture_recoveries")) == 1
    assert len(f.sql("SELECT * FROM crossref_harvest_page_attempts")) == 1
