"""Task #98: journal postconditions and original attachment authority on replay.

Fault triggers model failed/partial writes, not a claim to secure an attacker-owned
SQLite database. Uses the existing v15 fixture; no provider transport is present.
"""

from datetime import timedelta, timezone

import pytest

from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.exceptions.crossref_attachment_error import CrossrefAttachmentError as Error
from libs.discovery.tests.integration.test_crossref_claimed_attachment import Fixture, NOW


@pytest.fixture
def f(tmp_path):
    return Fixture(tmp_path / "runtime")


def snapshot(f):
    tables = (
        "crossref_harvest_page_attempts", "crossref_harvest_pages", "crossref_harvest_passes",
        "crossref_harvest_windows", "crossref_capture_claims", "crossref_capture_inbox",
        "workspace_metadata", "object_registry",
    )
    return tuple(tuple(tuple(row) for row in f.sql("SELECT * FROM " + table)) for table in tables)


@pytest.mark.parametrize("status", [200, 429])
def test_ignored_attempt_insert_never_commits_a_page_without_receipt_history(f, status):
    f.stage(status)
    f.sql("CREATE TRIGGER ignore_attempt BEFORE INSERT ON crossref_harvest_page_attempts "
          "BEGIN SELECT RAISE(IGNORE); END")
    before = snapshot(f)
    with pytest.raises(Error, match="outcome_conflict"):
        f.attach()
    assert snapshot(f) == before
    f.sql("DROP TRIGGER ignore_attempt")
    assert f.attach().replayed is False


@pytest.mark.parametrize("mutation", [
    "UPDATE crossref_harvest_page_attempts SET id='wrong' WHERE id=NEW.id",
    "UPDATE crossref_harvest_page_attempts SET recorded_at='not-time' WHERE id=NEW.id",
    "UPDATE crossref_harvest_page_attempts SET recorded_at='2026-09-25T00:00:01+00:00' WHERE id=NEW.id",
    "DELETE FROM crossref_harvest_page_attempts WHERE id=NEW.id",
])
def test_attempt_trigger_corruption_is_detected_before_commit(f, mutation):
    f.stage()
    f.sql("CREATE TRIGGER alter_attempt AFTER INSERT ON crossref_harvest_page_attempts "
          "BEGIN " + mutation + "; END")
    before = snapshot(f)
    with pytest.raises(Error):
        f.attach()
    assert snapshot(f) == before


@pytest.mark.parametrize("mutation", [
    "UPDATE crossref_harvest_pages SET successful_receipt_id=NULL WHERE id=NEW.id",
    "UPDATE crossref_harvest_pages SET last_error_code='injected' WHERE id=NEW.id",
    "UPDATE crossref_harvest_pages SET request_fingerprint='injected' WHERE id=NEW.id",
    "UPDATE object_registry SET state='quarantined'",
    "UPDATE workspace_metadata SET epoch=epoch+1",
    "UPDATE crossref_harvest_passes SET current_cursor='later',next_page_no=1",
    "UPDATE crossref_harvest_passes SET state='failed'",
])
def test_final_page_and_authority_are_rechecked_in_the_writer_transaction(f, mutation):
    f.stage()
    f.sql("CREATE TRIGGER alter_page AFTER UPDATE ON crossref_harvest_pages "
          "BEGIN " + mutation + "; END")
    before = snapshot(f)
    with pytest.raises(Error):
        f.attach()
    assert snapshot(f) == before


def test_ignored_page_update_rolls_back_the_attempt_insert(f):
    f.stage()
    f.sql("CREATE TRIGGER ignore_page BEFORE UPDATE ON crossref_harvest_pages "
          "BEGIN SELECT RAISE(IGNORE); END")
    before = snapshot(f)
    with pytest.raises(Error):
        f.attach()
    assert snapshot(f) == before


@pytest.mark.parametrize("recorded_at", [
    "", "not-time", "2026-09-25T00:00:00",
    (NOW - timedelta(microseconds=1)).isoformat(),
    (NOW + timedelta(seconds=60)).isoformat(),
    (NOW + timedelta(days=1)).isoformat(),
])
def test_replay_requires_a_valid_original_attachment_time(f, recorded_at):
    f.stage()
    f.attach()
    f.sql("UPDATE crossref_harvest_page_attempts SET recorded_at=?", (recorded_at,))
    before = snapshot(f)
    with pytest.raises(Error, match="outcome_conflict"):
        f.store.replay(f.claim, f.stored)
    assert snapshot(f) == before


def test_replay_cannot_predate_durable_inbox_staging(f):
    capture = CrossrefHttpCapture(200, (), b"{}", NOW, True, None)
    f.inbox.stage(f.claim, capture, staged_at=NOW + timedelta(seconds=5))
    stored = f.publisher(f.claim)
    from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
    decision = CrossrefRatePolicy().evaluate(200, (), now=NOW)
    f.store.attach(f.claim, stored, decision, attached_at=NOW + timedelta(seconds=6))
    f.sql("UPDATE crossref_harvest_page_attempts SET recorded_at=?",
          ((NOW + timedelta(seconds=4)).isoformat(),))
    with pytest.raises(Error, match="outcome_conflict"):
        f.store.replay(f.claim, stored)


@pytest.mark.parametrize("mutation", [
    "UPDATE crossref_harvest_pages SET state='captured'",
    "UPDATE crossref_harvest_pages SET successful_receipt_id=(SELECT receipt_id "
    "FROM crossref_harvest_page_attempts LIMIT 1)",
    "UPDATE crossref_harvest_pages SET last_error_code='wrong'",
])
def test_failure_replay_cannot_hide_a_contradictory_page_projection(f, mutation):
    f.stage(429)
    f.attach()
    f.sql(mutation)
    before = snapshot(f)
    with pytest.raises(Error, match="outcome_conflict"):
        f.store.replay(f.claim, f.stored)
    assert snapshot(f) == before


@pytest.mark.parametrize("status", [200, 403, 429])
def test_exact_historical_replay_does_not_renew_lease_or_change_any_rows(f, status):
    f.stage(status)
    first = f.attach()
    before = snapshot(f)
    again = f.store.attach(f.claim, f.stored, f.decision, attached_at=NOW + timedelta(days=1))
    assert again.replayed and again.attempt_id == first.attempt_id
    assert snapshot(f) == before


def test_equivalent_timezone_timestamp_remains_a_valid_readonly_replay(f):
    f.stage()
    f.attach()
    offset_time = NOW.astimezone(timezone(timedelta(hours=8))).isoformat()
    f.sql("UPDATE crossref_harvest_page_attempts SET recorded_at=?", (offset_time,))
    before = snapshot(f)
    assert f.store.replay(f.claim, f.stored).replayed
    assert snapshot(f) == before


def test_later_parser_error_does_not_invalidate_original_successful_attachment(f):
    f.stage()
    f.attach()
    f.sql("UPDATE crossref_harvest_pages SET last_error_code='parser_failed'")
    before = snapshot(f)
    assert f.store.replay(f.claim, f.stored).replayed
    assert snapshot(f) == before
