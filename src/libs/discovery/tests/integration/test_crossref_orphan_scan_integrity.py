"""S4a fault injection: canonical SQLite, real receipt codec, disk kernel-port fixture.

The fixture is not the full kernel bootstrap/publisher. No provider transport exists here.
"""

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.kernel_crossref_capture_store_adapter import (
    KernelCrossrefCaptureStoreAdapter,
)
from libs.discovery.adapters.driven.posix_crossref_rate_gate_adapter import PosixCrossrefRateGateAdapter
from libs.discovery.adapters.driven.sqlite_crossref_orphan_receipt_recovery_adapter import (
    SqliteCrossrefOrphanReceiptRecoveryAdapter,
)
from libs.discovery.domain.services.crossref_rate_policy import CrossrefRatePolicy
from libs.discovery.dtos.crossref_capture import CrossrefHttpCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.exceptions.crossref_capture_recovery_error import CrossrefCaptureRecoveryError

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
PAGE = "crossref-page:fixture"
PASS = "crossref-pass:fixture"
WINDOW = "crossref-window:fixture"


class Gate:
    def __init__(self):
        self.observations = []

    @contextmanager
    def slot(self, contact_email):
        assert contact_email == "fixture@example.invalid"
        yield self

    def observe(self, status, headers, *, capture_error=None):
        self.observations.append((status, headers, capture_error))
        return CrossrefRatePolicy().evaluate(status, headers, now=NOW, capture_error=capture_error)


class Fixture:
    def __init__(self, root):
        self.root = root
        self.path = root / "journal.sqlite3"
        self.reads = []
        self.source = CrossrefSourceAdapter()
        self.plan = self.source.compile(CrossrefWindowInput(
            "binding:fixture", "statistics", NOW - timedelta(days=2), NOW - timedelta(days=1),
            "fixture@example.invalid", "v1", 2,
        ))
        self.request = self.source.page(self.plan)
        repo = Path(__file__).resolve().parents[5]
        connection = self.connect()
        for name in ("0001-object-registry.sql", "0011-crossref-harvest.sql"):
            connection.executescript((repo / "migrations" / name).read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO crossref_harvest_windows VALUES(?,?,?,?,?,?,?,'running',?,?)",
            (WINDOW, "binding:fixture", self.plan.query_fingerprint, "v1",
             self.plan.definition.from_index.isoformat(), self.plan.definition.until_index.isoformat(),
             2, NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO crossref_harvest_passes "
            "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
            "VALUES(?,?,1,?,'running','*',?)",
            (PASS, WINDOW, self.plan.parameters_fingerprint, NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO crossref_harvest_pages "
            "(id,pass_id,page_no,cursor_in,request_fingerprint,state,created_at) "
            "VALUES(?,?,0,'*',?,'requested',?)",
            (PAGE, PASS, self.request.request_fingerprint, NOW.isoformat()),
        )
        connection.close()
        self.captures = KernelCrossrefCaptureStoreAdapter(self.publish, self.read)
        self.gate = Gate()

    def connect(self):
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=1)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def sql(self, query, values=()):
        connection = self.connect()
        try:
            return connection.execute(query, values).fetchall()
        finally:
            connection.close()

    def publish(self, content, kind="raw", media_type="application/octet-stream",
                retention_policy="source-response"):
        digest = hashlib.sha256(content).hexdigest()
        object_id = kind + ":" + digest
        relative = f"objects/{kind}/{digest}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        self.sql(
            "INSERT OR IGNORE INTO object_registry VALUES(?,?,?,?,?,?,?,?,?)",
            (object_id, digest, relative, kind, media_type, len(content), "available",
             NOW.isoformat(), retention_policy),
        )
        return SimpleNamespace(object_id=object_id, content_sha256=digest,
                               byte_size=len(content), state="available")

    def read(self, object_id):
        self.reads.append(object_id)
        row = self.sql("SELECT * FROM object_registry WHERE object_id=?", (object_id,))[0]
        if row["state"] != "available":
            raise RuntimeError("fixture_object_unavailable")
        content = (self.root / row["relative_path"]).read_bytes()
        if len(content) != row["byte_size"] or hashlib.sha256(content).hexdigest() != row["content_sha256"]:
            raise RuntimeError("fixture_object_corrupt")
        return content

    def receipt(self, *, request=None, status=200, headers=(), body=b'{}', attempt_key=PAGE):
        return self.captures.save(request or self.request,
                                  CrossrefHttpCapture(status, headers, body, NOW, True, None),
                                  attempt_key=attempt_key)

    def recovery(self, **kwargs):
        return SqliteCrossrefOrphanReceiptRecoveryAdapter(
            self.connect, self.read, self.captures, self.gate, **kwargs,
        )

    def find(self, **kwargs):
        return self.recovery(**kwargs).find(self.plan, PAGE, self.request)


@pytest.fixture
def fixture(tmp_path):
    return Fixture(tmp_path)


def test_unique_receipt_recovers_readonly_and_empty_registry_returns_none(fixture):
    assert fixture.find() is None
    receipt_id = fixture.receipt()
    before = tuple(tuple(r) for r in fixture.sql("SELECT * FROM crossref_harvest_pages"))
    result = fixture.find()
    assert (result.receipt_id, result.action, result.failure_code) == (receipt_id, "accept", None)
    assert tuple(tuple(r) for r in fixture.sql("SELECT * FROM crossref_harvest_pages")) == before
    assert fixture.sql("SELECT * FROM crossref_harvest_page_attempts") == []


@pytest.mark.parametrize("date_value", [
    (NOW - timedelta(days=1)).isoformat(), "not-a-date", "", "2026-09-24T12:00:00+08:00",
])
def test_object_timestamp_cannot_hide_an_existing_receipt(fixture, date_value):
    receipt_id = fixture.receipt()
    fixture.sql("UPDATE object_registry SET created_at=?", (date_value,))
    result = fixture.find()
    assert result is not None and result.receipt_id == receipt_id


@pytest.mark.parametrize("date_value", ["not-a-date", "", (NOW + timedelta(days=1)).isoformat()])
def test_page_clock_cannot_turn_existing_receipt_into_absence(fixture, date_value):
    receipt_id = fixture.receipt()
    fixture.sql("UPDATE crossref_harvest_pages SET created_at=?", (date_value,))
    result = fixture.find()
    assert result is not None and result.receipt_id == receipt_id


@pytest.mark.parametrize("state", ["missing", "quarantined"])
def test_unavailable_receipt_is_unknown_not_absent(fixture, state):
    receipt_id = fixture.receipt()
    fixture.sql("UPDATE object_registry SET state=? WHERE object_id=?", (state, receipt_id))
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_object_unreadable"):
        fixture.find()
    assert fixture.gate.observations == []


def test_corrupt_content_is_not_treated_as_unrelated_body(fixture):
    receipt_id = fixture.receipt()
    row = fixture.sql("SELECT relative_path FROM object_registry WHERE object_id=?", (receipt_id,))[0]
    (fixture.root / row[0]).write_bytes(b"damaged")
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_object_unreadable"):
        fixture.find()


@pytest.mark.parametrize("state", ["failed", "completed"])
def test_inactive_pass_cannot_recover_into_a_current_attempt(fixture, state):
    fixture.receipt()
    fixture.sql("UPDATE crossref_harvest_passes SET state=?", (state,))
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_page_not_active"):
        fixture.find()
    assert fixture.gate.observations == []


@pytest.mark.parametrize("column,value", [("current_cursor", "advanced"), ("next_page_no", 1)])
def test_stale_page_checkpoint_cannot_recover(fixture, column, value):
    fixture.receipt()
    fixture.sql(f"UPDATE crossref_harvest_passes SET {column}=?", (value,))
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_page_not_active"):
        fixture.find()


def test_contact_in_plan_must_match_the_exact_request(fixture):
    fixture.receipt()
    other = fixture.source.compile(replace(fixture.plan.definition, contact_email="other@example.invalid"))
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_request_mismatch"):
        fixture.recovery().find(other, PAGE, fixture.request)
    assert fixture.gate.observations == []


def test_forged_request_url_cannot_reuse_another_request_fingerprint(fixture):
    request = replace(fixture.request, url=fixture.request.url + "&unexpected=1")
    fixture.receipt(request=request)
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_request_mismatch"):
        fixture.recovery().find(fixture.plan, PAGE, request)
    assert fixture.gate.observations == []


def test_wrong_receipt_request_and_multiple_receipts_fail_closed(fixture):
    other = fixture.source.page(fixture.plan, "another-cursor")
    fixture.receipt(request=other)
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_request_mismatch"):
        fixture.find()
    fixture.receipt()
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_receipt_ambiguous"):
        fixture.find()


def test_scan_count_limit_checks_before_object_io(fixture):
    fixture.receipt()
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_scan_incomplete"):
        fixture.find(maximum_scan=1)
    assert fixture.reads == []


def test_scan_byte_limit_checks_before_object_io(fixture):
    fixture.receipt()
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_scan_byte_limit"):
        fixture.find(maximum_scan_bytes=1)
    assert fixture.reads == []


@pytest.mark.parametrize("limit", [True, 0, -1, 1.0, 512_000_001])
def test_invalid_byte_budget_is_rejected(fixture, limit):
    with pytest.raises(CrossrefCaptureRecoveryError, match="invalid_crossref_orphan_scan_byte_limit"):
        fixture.recovery(maximum_scan_bytes=limit)


def test_new_receipt_during_scan_requires_retry_not_absence(fixture):
    fixture.publish(b"unrelated raw")
    injected = False

    def read_with_publish(object_id):
        nonlocal injected
        content = fixture.read(object_id)
        if not injected:
            injected = True
            fixture.receipt()
        return content

    recovery = SqliteCrossrefOrphanReceiptRecoveryAdapter(
        fixture.connect, read_with_publish, fixture.captures, fixture.gate,
    )
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_scan_changed"):
        recovery.find(fixture.plan, PAGE, fixture.request)
    assert fixture.gate.observations == []
    assert fixture.find() is not None


def test_nonbytes_reader_result_cannot_authorize_refetch(fixture):
    fixture.publish(b"unrelated")
    recovery = SqliteCrossrefOrphanReceiptRecoveryAdapter(
        fixture.connect, lambda _: "not bytes", fixture.captures, fixture.gate,
    )
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_object_unreadable"):
        recovery.find(fixture.plan, PAGE, fixture.request)


@pytest.mark.parametrize("status,headers,action,code", [
    (200, (), "accept", None),
    (403, (), "stop", "crossref_forbidden"),
    (429, (("retry-after", "3600"),), "retry", "crossref_rate_limited"),
    (503, (), "retry", "crossref_server_error"),
    (302, (("location", "https://publisher.invalid/fulltext"),), "stop", "crossref_redirect"),
])
def test_original_http_policy_is_reconciled_not_bypassed(fixture, status, headers, action, code):
    receipt_id = fixture.receipt(status=status, headers=headers)
    result = fixture.find()
    assert (result.receipt_id, result.action, result.failure_code) == (receipt_id, action, code)
    assert fixture.gate.observations == [(status, headers, None)]


def test_real_posix_403_circuit_remains_closed_across_reopen(fixture):
    receipt_id = fixture.receipt(status=403)
    directory = fixture.root / "shared-gate"
    directory.mkdir(mode=0o700)
    fixture.gate = PosixCrossrefRateGateAdapter(
        directory, "fixture@example.invalid", clock=lambda: NOW.timestamp(), random_value=lambda: 0.5,
    )
    first = fixture.find()
    assert first.action == "stop" and first.receipt_id == receipt_id
    reopened = PosixCrossrefRateGateAdapter(
        directory, "fixture@example.invalid", clock=lambda: NOW.timestamp() + 100,
    )
    fixture.gate = reopened
    second = fixture.find()
    assert second.action == "stop" and second.failure_code == "crossref_circuit_open"


def test_newer_pass_generation_makes_old_running_pass_ineligible(fixture):
    fixture.receipt()
    fixture.sql(
        "INSERT INTO crossref_harvest_passes "
        "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
        "VALUES(?,?,2,?,'running','*',?)",
        ("crossref-pass:newer", WINDOW, fixture.plan.parameters_fingerprint, NOW.isoformat()),
    )
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_page_not_active"):
        fixture.find()
    assert fixture.gate.observations == []


@pytest.mark.parametrize("window_state", ["failed", "traversed", "repair_pending"])
def test_window_state_must_agree_with_active_page(fixture, window_state):
    fixture.receipt()
    fixture.sql("UPDATE crossref_harvest_windows SET state=?", (window_state,))
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_page_not_active"):
        fixture.find()
    assert fixture.gate.observations == []


def test_claim_changes_during_gate_observation_cannot_adopt_old_receipt(fixture):
    fixture.receipt()

    class AdvancingGate(Gate):
        def observe(self, status, headers, *, capture_error=None):
            decision = super().observe(status, headers, capture_error=capture_error)
            fixture.sql("UPDATE crossref_harvest_passes SET current_cursor='advanced'")
            return decision

    fixture.gate = AdvancingGate()
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_page_not_active"):
        fixture.find()
    assert len(fixture.gate.observations) == 1


def test_second_matching_receipt_during_gate_observation_is_not_silently_chosen(fixture):
    fixture.receipt()

    class PublishingGate(Gate):
        def observe(self, status, headers, *, capture_error=None):
            decision = super().observe(status, headers, capture_error=capture_error)
            fixture.receipt(body=b'{"version":"new"}')
            return decision

    fixture.gate = PublishingGate()
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_scan_changed"):
        fixture.find()


def test_circuit_open_cannot_skip_final_snapshot_recheck(fixture):
    from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError

    fixture.receipt(status=403)

    class RacingCircuit:
        @contextmanager
        def slot(self, contact_email):
            fixture.sql("UPDATE crossref_harvest_passes SET state='failed'")
            raise CrossrefRateError("crossref_circuit_open")
            yield  # pragma: no cover - context manager signature

    fixture.gate = RacingCircuit()
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_page_not_active"):
        fixture.find()


def test_linked_receipt_is_excluded_but_unlinked_body_does_not_become_receipt(fixture):
    receipt_id = fixture.receipt(attempt_key="crossref-page:other")
    fixture.sql(
        "INSERT INTO crossref_harvest_pages "
        "(id,pass_id,page_no,cursor_in,request_fingerprint,state,created_at) "
        "VALUES(?,?,1,'other',?,'captured',?)",
        ("crossref-page:other", PASS, "f" * 64, NOW.isoformat()),
    )
    fixture.sql(
        "INSERT INTO crossref_harvest_page_attempts VALUES(?,?,1,?,'accept',NULL,?)",
        ("attempt:linked", "crossref-page:other", receipt_id, NOW.isoformat()),
    )
    assert fixture.find() is None
    assert receipt_id not in fixture.reads


def test_byte_budget_allows_exact_registered_size(fixture):
    receipt_id = fixture.receipt()
    total = fixture.sql("SELECT SUM(byte_size) FROM object_registry")[0][0]
    assert fixture.find(maximum_scan_bytes=total).receipt_id == receipt_id
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_scan_byte_limit"):
        fixture.find(maximum_scan_bytes=total - 1)


def test_registry_hash_mismatch_is_visible_before_read(fixture):
    receipt_id = fixture.receipt()
    fixture.sql("UPDATE object_registry SET content_sha256=? WHERE object_id=?", ("f" * 64, receipt_id))
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_registry_corrupt"):
        fixture.find()
    assert fixture.reads == []


def test_damaged_selected_receipt_from_capture_port_is_not_accepted(fixture):
    fixture.receipt()
    fixture.captures = SimpleNamespace(read=lambda _: object())
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_request_mismatch"):
        fixture.find()
    assert fixture.gate.observations == []


def test_gate_failure_is_not_misreported_as_absence(fixture):
    from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError

    fixture.receipt()

    class BrokenGate:
        @contextmanager
        def slot(self, contact_email):
            raise CrossrefRateError("crossref_rate_state_corrupt")
            yield  # pragma: no cover - context manager signature

    fixture.gate = BrokenGate()
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_gate_reconcile_failed"):
        fixture.find()


@pytest.mark.parametrize("window_state", ["failed", "traversed", "repair_pending"])
def test_running_repair_generation_can_recover_without_resetting_window_history(fixture, window_state):
    connection = fixture.connect()
    root = Path(__file__).resolve().parents[5]
    connection.executescript((root / "migrations/0012-crossref-repair.sql").read_text(encoding="utf-8"))
    connection.close()
    fixture.sql("UPDATE crossref_harvest_windows SET state=?", (window_state,))
    fixture.sql("UPDATE crossref_harvest_passes SET pass_no=2")
    fixture.sql(
        "INSERT INTO crossref_harvest_passes "
        "(id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at,finished_at) "
        "VALUES(?,?,1,?,'failed',NULL,?,?)",
        ("crossref-pass:historical", WINDOW, fixture.plan.parameters_fingerprint,
         (NOW - timedelta(hours=1)).isoformat(), NOW.isoformat()),
    )
    fixture.sql(
        "INSERT INTO crossref_repair_runs "
        "(id,window_id,repair_no,pass_id,reason,state,created_at) VALUES(?,?,1,?,'operator','running',?)",
        ("repair:current", WINDOW, PASS, NOW.isoformat()),
    )
    receipt_id = fixture.receipt()
    assert fixture.find().receipt_id == receipt_id
    assert fixture.sql("SELECT state FROM crossref_harvest_windows")[0][0] == window_state
    fixture.sql("UPDATE crossref_repair_runs SET state='failed',finished_at=?", (NOW.isoformat(),))
    with pytest.raises(CrossrefCaptureRecoveryError, match="crossref_orphan_page_not_active"):
        fixture.find()
