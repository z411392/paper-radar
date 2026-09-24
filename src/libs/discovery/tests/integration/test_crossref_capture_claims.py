"""Author-owned pre-send claim contract; real SQLite, no network transport."""

import sqlite3
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.sqlite_crossref_capture_claim_store_adapter import (
    SqliteCrossrefCaptureClaimStoreAdapter,
)
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.exceptions.crossref_capture_claim_error import CrossrefCaptureClaimError

NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
PASS = "crossref-pass:claims"
WINDOW = "crossref-window:claims"


class Fixture:
    def __init__(self, root):
        self.path = root / "claims.sqlite3"
        self.source = CrossrefSourceAdapter()
        self.plan = self.source.compile(CrossrefWindowInput(
            "binding:claims", "statistics", NOW - timedelta(days=2), NOW - timedelta(days=1),
            "fixture@example.invalid", "v1", 2,
        ))
        self.request = self.source.page(self.plan)
        repo = Path(__file__).resolve().parents[5]
        connection = self.connect()
        for name in ("0001-object-registry.sql", "0011-crossref-harvest.sql",
                     "0012-crossref-repair.sql", "0013-crossref-window-splits.sql",
                     "0014-crossref-capture-claims.sql"):
            connection.executescript((repo / "migrations" / name).read_text(encoding="utf-8"))
        connection.execute("INSERT INTO workspace_metadata VALUES(1,'workspace:claims',1,1,?,NULL)",
                           (NOW.isoformat(),))
        connection.execute(
            "INSERT INTO crossref_harvest_windows VALUES(?,?,?,?,?,?,?,'running',?,?)",
            (WINDOW, "binding:claims", self.plan.query_fingerprint, "v1",
             self.plan.definition.from_index.isoformat(), self.plan.definition.until_index.isoformat(),
             2, NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO crossref_harvest_passes "
            "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
            "VALUES(?,?,1,?,'running','*',?)",
            (PASS, WINDOW, self.plan.parameters_fingerprint, NOW.isoformat()),
        )
        connection.close()
        self.store = SqliteCrossrefCaptureClaimStoreAdapter(self.connect)

    def connect(self):
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def sql(self, query, values=()):
        connection = self.connect()
        try:
            return connection.execute(query, values).fetchall()
        finally:
            connection.close()

    def reserve(self, **changes):
        values = dict(owner_id="worker:a", expected_workspace_id="workspace:claims",
                      expected_epoch=1, now=NOW, lease_seconds=60)
        values.update(changes)
        return self.store.reserve(self.plan, PASS, self.request, **values)


@pytest.fixture
def fixture(tmp_path):
    return Fixture(tmp_path)


def test_reservation_reopens_without_extending_lease_or_recreating_page(fixture):
    first = fixture.reserve()
    fixture.store = SqliteCrossrefCaptureClaimStoreAdapter(fixture.connect)
    second = fixture.reserve(now=NOW + timedelta(seconds=1))
    assert second == first
    assert first.fencing_token == 1 and first.state == "reserved"
    assert first.lease_until == NOW + timedelta(seconds=60)
    assert len(fixture.sql("SELECT * FROM crossref_harvest_pages")) == 1
    assert len(fixture.sql("SELECT * FROM crossref_capture_claims")) == 1
    assert fixture.store.read(first.claim_id) == first


def test_other_owner_cannot_reserve_until_unstarted_lease_expires(fixture):
    old = fixture.reserve()
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_busy"):
        fixture.reserve(owner_id="worker:b", now=NOW + timedelta(seconds=59))
    new = fixture.reserve(owner_id="worker:b", now=NOW + timedelta(seconds=60))
    assert new.fencing_token == old.fencing_token + 1
    assert new.claim_id != old.claim_id and new.page_id == old.page_id
    assert fixture.store.read(old.claim_id).state == "expired"
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_fenced"):
        fixture.store.begin_dispatch(old, now=NOW + timedelta(seconds=61))
    fixture.store.begin_dispatch(new, now=NOW + timedelta(seconds=61))


def test_dispatch_authorization_is_single_use_even_same_owner(fixture):
    claim = fixture.reserve()
    fixture.store.begin_dispatch(claim, now=NOW)
    assert fixture.store.read(claim.claim_id).state == "dispatching"
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_dispatch_already_started"):
        fixture.store.begin_dispatch(claim, now=NOW + timedelta(seconds=1))


@pytest.mark.parametrize("owner", ["worker:a", "worker:b"])
def test_uncertain_dispatch_never_becomes_retryable_just_because_lease_expired(fixture, owner):
    claim = fixture.reserve()
    fixture.store.begin_dispatch(claim, now=NOW)
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_outcome_unknown"):
        fixture.reserve(owner_id=owner, now=NOW + timedelta(days=30))
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_dispatch_already_started"):
        fixture.store.release(claim, now=NOW + timedelta(days=30))
    assert len(fixture.sql("SELECT * FROM crossref_capture_claims")) == 1


def test_release_is_idempotent_and_allows_new_token_without_erasing_history(fixture):
    old = fixture.reserve()
    fixture.store.release(old, now=NOW)
    fixture.store.release(old, now=NOW + timedelta(seconds=1))
    new = fixture.reserve(owner_id="worker:b", now=NOW + timedelta(seconds=2))
    assert fixture.store.read(old.claim_id).state == "released"
    assert new.fencing_token == 2
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_fenced"):
        fixture.store.release(old, now=NOW + timedelta(seconds=3))


@pytest.mark.parametrize("mutation", [
    "UPDATE workspace_metadata SET external_effects_enabled=0",
    "UPDATE workspace_metadata SET epoch=2",
    "UPDATE workspace_metadata SET workspace_id='workspace:restored'",
])
def test_master_gate_and_restore_identity_rechecked_before_dispatch(fixture, mutation):
    claim = fixture.reserve()
    fixture.sql(mutation)
    with pytest.raises(CrossrefCaptureClaimError):
        fixture.store.begin_dispatch(claim, now=NOW)
    assert fixture.store.read(claim.claim_id).state == "reserved"


def test_disabled_workspace_does_not_create_even_a_page(fixture):
    fixture.sql("UPDATE workspace_metadata SET external_effects_enabled=0")
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_effects_disabled"):
        fixture.reserve()
    assert fixture.sql("SELECT * FROM crossref_harvest_pages") == []
    assert fixture.sql("SELECT * FROM crossref_capture_claims") == []


@pytest.mark.parametrize("mutation", [
    "UPDATE crossref_harvest_passes SET state='failed'",
    "UPDATE crossref_harvest_passes SET current_cursor='next'",
    "UPDATE crossref_harvest_passes SET next_page_no=1",
    "UPDATE crossref_harvest_pages SET state='captured'",
])
def test_changed_page_or_pass_cannot_dispatch(fixture, mutation):
    claim = fixture.reserve()
    fixture.sql(mutation)
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_page_not_active"):
        fixture.store.begin_dispatch(claim, now=NOW)
    assert fixture.store.read(claim.claim_id).state == "reserved"


def test_newer_pass_generation_fences_old_reserved_page(fixture):
    claim = fixture.reserve()
    fixture.sql(
        "INSERT INTO crossref_harvest_passes "
        "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
        "VALUES(?,?,2,?,'running','*',?)",
        ("crossref-pass:new", WINDOW, fixture.plan.parameters_fingerprint, NOW.isoformat()),
    )
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_page_not_active"):
        fixture.store.begin_dispatch(claim, now=NOW)


@pytest.mark.parametrize("window_state", ["failed", "traversed", "repair_pending"])
def test_only_explicit_running_repair_can_use_historical_window_state(fixture, window_state):
    fixture.sql("UPDATE crossref_harvest_windows SET state=?", (window_state,))
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_page_not_active"):
        fixture.reserve()
    fixture.sql(
        "INSERT INTO crossref_repair_runs "
        "(id,window_id,repair_no,pass_id,reason,state,created_at) VALUES(?,?,1,?,'operator','running',?)",
        ("repair:claims", WINDOW, PASS, NOW.isoformat()),
    )
    claim = fixture.reserve()
    fixture.sql("UPDATE crossref_repair_runs SET state='failed'")
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_page_not_active"):
        fixture.store.begin_dispatch(claim, now=NOW)


def test_legacy_requested_page_cannot_be_assumed_to_have_no_orphan(fixture):
    fixture.sql(
        "INSERT INTO crossref_harvest_pages "
        "(id,pass_id,page_no,cursor_in,request_fingerprint,state,created_at) "
        "VALUES('page:legacy',?,0,'*',?,'requested',?)",
        (PASS, fixture.request.request_fingerprint, NOW.isoformat()),
    )
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_legacy_page_requires_recovery"):
        fixture.reserve()
    assert fixture.sql("SELECT * FROM crossref_capture_claims") == []


def test_forged_plan_request_or_owner_is_rejected(fixture):
    bad = replace(fixture.request, url=fixture.request.url + "&injected=1")
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_request_mismatch"):
        fixture.store.reserve(fixture.plan, PASS, bad, owner_id="worker:a",
                              expected_workspace_id="workspace:claims", expected_epoch=1,
                              now=NOW, lease_seconds=60)
    claim = fixture.reserve()
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_fenced"):
        fixture.store.begin_dispatch(replace(claim, owner_id="worker:forged"), now=NOW)


@pytest.mark.parametrize("offset,code", [(-1, "crossref_claim_clock_regressed"), (60, "crossref_claim_expired")])
def test_claim_clock_and_expiry_fail_closed(fixture, offset, code):
    claim = fixture.reserve()
    with pytest.raises(CrossrefCaptureClaimError, match=code):
        fixture.store.begin_dispatch(claim, now=NOW + timedelta(seconds=offset))


@pytest.mark.parametrize("seconds", [True, 0, -1, 1.5, 86401])
def test_invalid_lease_does_not_write(fixture, seconds):
    with pytest.raises(CrossrefCaptureClaimError, match="invalid_crossref_claim_lease"):
        fixture.reserve(lease_seconds=seconds)
    assert fixture.sql("SELECT * FROM crossref_harvest_pages") == []


def test_claim_insert_failure_rolls_back_page_creation(fixture):
    fixture.sql("CREATE TRIGGER fail_claim BEFORE INSERT ON crossref_capture_claims "
                "BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(CrossrefCaptureClaimError):
        fixture.reserve()
    assert fixture.sql("SELECT * FROM crossref_harvest_pages") == []


def test_dispatch_failure_rolls_back_reserved_state(fixture):
    claim = fixture.reserve()
    fixture.sql("CREATE TRIGGER fail_dispatch BEFORE UPDATE ON crossref_capture_claims "
                "WHEN NEW.state='dispatching' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(CrossrefCaptureClaimError):
        fixture.store.begin_dispatch(claim, now=NOW)
    assert fixture.store.read(claim.claim_id).state == "reserved"


def _race(actions):
    gate = threading.Barrier(len(actions))
    outcomes = []
    def run(action):
        try:
            gate.wait(timeout=3)
            outcomes.append(("ok", action()))
        except Exception as exc:
            outcomes.append(("error", exc))
    threads = [threading.Thread(target=run, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    return outcomes


def test_concurrent_reservers_create_only_one_live_claim(fixture):
    outcomes = _race([lambda: fixture.reserve(owner_id="worker:a"),
                      lambda: fixture.reserve(owner_id="worker:b")])
    assert [item[0] for item in outcomes].count("ok") == 1
    errors = [str(value) for state, value in outcomes if state == "error"]
    assert errors == ["crossref_claim_busy"]
    assert len(fixture.sql("SELECT * FROM crossref_capture_claims")) == 1


def test_concurrent_dispatchers_cannot_reuse_the_same_lease(fixture):
    claim = fixture.reserve()
    outcomes = _race([lambda: fixture.store.begin_dispatch(claim, now=NOW),
                      lambda: fixture.store.begin_dispatch(claim, now=NOW)])
    assert [item[0] for item in outcomes].count("ok") == 1
    errors = [str(value) for state, value in outcomes if state == "error"]
    assert errors == ["crossref_claim_dispatch_already_started"]


def test_claim_history_cannot_be_deleted_or_identity_rewritten(fixture):
    claim = fixture.reserve()
    with pytest.raises(sqlite3.IntegrityError):
        fixture.sql("DELETE FROM crossref_capture_claims WHERE id=?", (claim.claim_id,))
    with pytest.raises(sqlite3.IntegrityError):
        fixture.sql("UPDATE crossref_capture_claims SET owner_id='other' WHERE id=?", (claim.claim_id,))


def test_other_journal_writer_attaching_receipt_revokes_dispatch(fixture):
    claim = fixture.reserve()
    fixture.sql("INSERT INTO object_registry VALUES(?,?,?,'raw','application/json',2,'available',?,?)",
                ("raw:" + "a" * 64, "a" * 64, "objects/raw/fixture", NOW.isoformat(), "source-response"))
    fixture.sql("INSERT INTO crossref_harvest_page_attempts VALUES(?,?,1,?,'retry','rate_limited',?)",
                ("attempt:older-protocol", claim.page_id, "raw:" + "a" * 64, NOW.isoformat()))
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_recovery_required"):
        fixture.store.begin_dispatch(claim, now=NOW)
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_recovery_required"):
        fixture.reserve(now=NOW + timedelta(seconds=60))


def test_clock_rollback_after_release_does_not_make_a_new_earlier_claim(fixture):
    claim = fixture.reserve()
    fixture.store.release(claim, now=NOW + timedelta(seconds=30))
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_clock_regressed"):
        fixture.reserve(now=NOW + timedelta(seconds=10))


def test_missing_claim_history_is_not_silently_accepted_as_a_new_page(fixture):
    claim = fixture.reserve()
    fixture.store.release(claim, now=NOW)
    with pytest.raises(sqlite3.IntegrityError):
        fixture.sql("UPDATE crossref_capture_claims SET state='reserved',ended_us=NULL WHERE id=?",
                    (claim.claim_id,))


@pytest.mark.parametrize("key,value", [("fencing_token", True), ("workspace_epoch", True)])
def test_boolean_identity_fields_cannot_impersonate_integer_token(fixture, key, value):
    claim = fixture.reserve()
    with pytest.raises(CrossrefCaptureClaimError, match="invalid_crossref_claim_identity"):
        fixture.store.begin_dispatch(replace(claim, **{key: value}), now=NOW)


def test_effects_change_after_rate_wait_does_not_dispatch(fixture):
    claim = fixture.reserve()
    # The eventual caller waits on a separate rate gate outside the DB transaction.
    fixture.sql("UPDATE workspace_metadata SET external_effects_enabled=0")
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_effects_disabled"):
        fixture.store.begin_dispatch(claim, now=NOW + timedelta(seconds=30))
    fixture.store.release(claim, now=NOW + timedelta(seconds=30))
    assert fixture.store.read(claim.claim_id).state == "released"


@pytest.mark.parametrize("mode", ["reserved", "dispatching"])
def test_process_exit_then_reopen_preserves_claim_phase(fixture, mode):
    import subprocess
    import sys
    script = """
import os, sqlite3, sys
from datetime import datetime, timedelta, timezone
from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.sqlite_crossref_capture_claim_store_adapter import (
    SqliteCrossrefCaptureClaimStoreAdapter,
)
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
now = datetime(2026,9,24,12,tzinfo=timezone.utc)
def connect():
    c=sqlite3.connect(sys.argv[1],isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c
source=CrossrefSourceAdapter()
plan=source.compile(CrossrefWindowInput('binding:claims','statistics',now-timedelta(days=2),
                                     now-timedelta(days=1),'fixture@example.invalid','v1',2))
store=SqliteCrossrefCaptureClaimStoreAdapter(connect)
claim=store.reserve(plan,'crossref-pass:claims',source.page(plan),owner_id='worker:subprocess',
                    expected_workspace_id='workspace:claims',expected_epoch=1,now=now,lease_seconds=60)
if sys.argv[2]=='dispatching':
    store.begin_dispatch(claim,now=now)
os._exit(37)
"""
    result = subprocess.run([sys.executable, "-c", script, str(fixture.path), mode],
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 37, result.stderr
    row = fixture.sql("SELECT id,state FROM crossref_capture_claims")[0]
    assert row["state"] == mode
    fixture.store = SqliteCrossrefCaptureClaimStoreAdapter(fixture.connect)
    if mode == "reserved":
        assert fixture.reserve(owner_id="worker:subprocess").claim_id == row["id"]
    else:
        with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_outcome_unknown"):
            fixture.reserve(now=NOW + timedelta(days=1))


def test_claim_operations_do_not_scan_unrelated_raw_history(fixture):
    connection = fixture.connect()
    try:
        connection.execute("BEGIN")
        for index in range(10000):
            digest = f"{index:064x}"
            connection.execute("INSERT INTO object_registry VALUES(?,?,?,'raw','text/plain',0,'available',?,?)",
                               ("raw:"+digest, digest, "objects/raw/"+digest, NOW.isoformat(), "fixture"))
        connection.commit()
    finally:
        connection.close()
    statements = []
    def connect():
        c = fixture.connect()
        c.set_trace_callback(statements.append)
        return c
    fixture.store = SqliteCrossrefCaptureClaimStoreAdapter(connect)
    claim = fixture.reserve()
    fixture.store.begin_dispatch(claim, now=NOW)
    assert not any("object_registry" in statement.lower() for statement in statements)


def test_foreign_keys_off_and_unsafe_journal_are_not_accepted(fixture):
    def bad_fk():
        c = fixture.connect()
        c.execute("PRAGMA foreign_keys=OFF")
        return c
    fixture.store = SqliteCrossrefCaptureClaimStoreAdapter(bad_fk)
    with pytest.raises(CrossrefCaptureClaimError, match="foreign_keys_required"):
        fixture.reserve()
    def bad_journal():
        c = fixture.connect()
        c.execute("PRAGMA journal_mode=MEMORY")
        return c
    fixture.store = SqliteCrossrefCaptureClaimStoreAdapter(bad_journal)
    with pytest.raises(CrossrefCaptureClaimError, match="crossref_claim_durable_journal_required"):
        fixture.reserve()
    assert fixture.sql("SELECT * FROM crossref_harvest_pages") == []
