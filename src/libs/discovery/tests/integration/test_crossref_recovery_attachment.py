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
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
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


class CircuitOpenGate:
    @contextmanager
    def slot(self, contact_email):
        assert contact_email == "fixture@example.invalid"
        raise CrossrefRateError("crossref_circuit_open")
        yield  # pragma: no cover


class DeferredGate:
    @contextmanager
    def slot(self, contact_email):
        assert contact_email == "fixture@example.invalid"
        raise CrossrefRateError("crossref_provider_deferred", 30.0)
        yield  # pragma: no cover


def test_open_circuit_allows_only_local_recovery_of_existing_response(f):
    f.stage(status=403)
    command = AttachClaimedCrossrefCapture(
        f.publisher,
        CircuitOpenGate(),
        f.store,
        source=CrossrefSourceAdapter(),
        clock=lambda: NOW + timedelta(seconds=61),
    )

    result = command(f.plan, f.claim)

    assert result.action == "stop"
    assert result.failure_code == "crossref_forbidden"
    assert len(f.sql("SELECT * FROM crossref_capture_recoveries")) == 1


def test_provider_deferred_does_not_attach_or_create_recovery_authority(f):
    f.stage(status=429, headers=(("retry-after", "3600"),))
    command = AttachClaimedCrossrefCapture(
        f.publisher,
        DeferredGate(),
        f.store,
        source=CrossrefSourceAdapter(),
        clock=lambda: NOW + timedelta(seconds=61),
    )

    with pytest.raises(CrossrefRateError, match="crossref_provider_deferred"):
        command(f.plan, f.claim)

    assert f.sql("SELECT * FROM crossref_capture_recoveries") == []
    assert f.sql("SELECT * FROM crossref_harvest_page_attempts") == []
