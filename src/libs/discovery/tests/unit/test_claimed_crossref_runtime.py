from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.application.commands.capture_claimed_crossref_response import (
    CaptureClaimedCrossrefResponse,
)
from libs.discovery.application.commands.run_claimed_crossref_harvest_window import (
    RunClaimedCrossrefHarvestWindow,
)
from libs.discovery.dtos.crossref_attachment import CrossrefAttachment
from libs.discovery.dtos.crossref_capture import (
    CrossrefHttpCapture,
    CrossrefStoredCapture,
)
from libs.discovery.dtos.crossref_capture_claim import CrossrefCaptureClaim
from libs.discovery.dtos.crossref_capture_resolution import CrossrefCaptureResolution
from libs.discovery.dtos.crossref_claimed_capture import CrossrefClaimedCapture
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.dtos.crossref_rate_decision import CrossrefRateDecision
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
from libs.kernel.dtos.workspace_info import WorkspaceInfo


NOW = datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc)


def plan_and_request():
    source = CrossrefSourceAdapter()
    plan = source.compile(
        CrossrefWindowInput(
            "binding:test",
            "statistics",
            NOW - timedelta(days=1),
            NOW,
            "reader@example.com",
            "v1",
            1000,
        )
    )
    return source, plan, source.page(plan)


def claim_for(request):
    return CrossrefCaptureClaim(
        "claim:test",
        "page:test",
        "pass:test",
        1,
        "worker:test",
        "workspace:test",
        1,
        request,
        NOW,
        NOW + timedelta(minutes=5),
        "reserved",
    )


class DeferredGate:
    @contextmanager
    def slot(self, contact_email):
        assert contact_email == "reader@example.com"
        raise CrossrefRateError("crossref_provider_deferred", 30.0)
        yield  # pragma: no cover


class RecordingClaims:
    def __init__(self, events):
        self.events = events
        self.begin_calls = 0
        self.reserve_calls = 0

    def begin_dispatch(self, claim, *, now):
        self.begin_calls += 1
        self.events.append("begin")

    def latest(self, page_id):
        self.events.append("latest")
        return None

    def reserve(self, *args, **kwargs):
        self.reserve_calls += 1
        self.events.append("reserve")
        return kwargs.pop("_claim", None)


class NoTransport:
    def __init__(self):
        self.calls = 0

    def get(self, plan, request):
        self.calls += 1
        raise AssertionError("transport must not run before provider slot")


def test_provider_deferred_before_slot_does_not_cross_send_boundary():
    source, plan, request = plan_and_request()
    events = []
    claims = RecordingClaims(events)
    transport = NoTransport()
    command = CaptureClaimedCrossrefResponse(
        transport,
        DeferredGate(),
        claims,
        SimpleNamespace(stage=lambda *args, **kwargs: None),
        lambda claim: None,
        source=source,
        clock=lambda: NOW,
    )

    with pytest.raises(CrossrefRateError, match="crossref_provider_deferred"):
        command(plan, claim_for(request))

    assert claims.begin_calls == 0
    assert transport.calls == 0


class RecordingLease:
    def __init__(self, events):
        self.events = events

    def observe(self, status, headers, *, capture_error=None):
        self.events.append("observe")
        return CrossrefRateDecision(
            "accept",
            None,
            0.0,
            1.0,
            None,
            None,
            (),
        )


class RecordingGate:
    def __init__(self, events):
        self.events = events

    @contextmanager
    def slot(self, contact_email):
        assert contact_email == "reader@example.com"
        self.events.append("slot")
        yield RecordingLease(self.events)


def test_claimed_capture_orders_slot_before_dispatch_and_stages_before_publish():
    source, plan, request = plan_and_request()
    events = []
    claims = RecordingClaims(events)
    capture = CrossrefHttpCapture(200, (), b"{}", NOW, True, None)
    stored = CrossrefStoredCapture(
        "raw:" + "a" * 64,
        "claim:test",
        request,
        capture,
        "raw:" + "b" * 64,
        "b" * 64,
    )

    class Transport:
        def get(self, plan, request):
            events.append("http")
            return capture

    class Inbox:
        def stage(self, claim, capture, *, staged_at):
            events.append("inbox")

    def publish(claim):
        events.append("publish")
        return stored

    command = CaptureClaimedCrossrefResponse(
        Transport(),
        RecordingGate(events),
        claims,
        Inbox(),
        publish,
        source=source,
        clock=lambda: NOW,
    )

    result = command(plan, claim_for(request))

    assert result.stored is stored
    assert result.decision.action == "accept"
    assert events == ["slot", "begin", "http", "inbox", "observe", "publish"]


class Journal:
    def __init__(self, request):
        self.request = request
        self.saved = 0
        self.commits = 0

    def ensure_window(self, plan, created_at):
        return SimpleNamespace(window_id="window:test")

    def start_pass(self, plan, started_at):
        return SimpleNamespace(
            pass_id="pass:test",
            state="running",
            current_cursor="*",
        )

    def begin_page(self, pass_id, request, created_at):
        assert request == self.request
        return SimpleNamespace(
            page_id="page:test",
            state="requested",
            successful_receipt_id=None,
        )

    def save_decoded(self, page_id, replayed, decoded_at):
        self.saved += 1

    def pending_items(self, page_id):
        return (SimpleNamespace(ordinal=0),)

    def commit_page(self, pass_id, page_id, committed_at):
        self.commits += 1
        return SimpleNamespace(
            pass_id=pass_id,
            state="completed",
            current_cursor=None,
        )


class RunnerClaims:
    def __init__(self, claim):
        self.claim = claim
        self.reserve_calls = 0

    def latest(self, page_id):
        return None

    def reserve(self, *args, **kwargs):
        self.reserve_calls += 1
        return self.claim


class Attachments:
    def __init__(self, stored):
        self.stored = stored

    def attach(self, claim, stored, decision, *, attached_at):
        return CrossrefAttachment(
            claim.claim_id,
            claim.page_id,
            stored.receipt_id,
            "attempt:test",
            "accept",
            None,
            False,
        )

    def resolve(self, claim, stored, *, resolved_at):
        return CrossrefCaptureResolution(
            claim.claim_id,
            "attempt:test",
            stored.receipt_id,
            "accept",
            None,
            resolved_at,
            None,
            "crossref-resolution-v1",
            False,
        )


def test_claimed_runner_stops_at_projection_without_advancing_cursor():
    source, plan, request = plan_and_request()
    claim = claim_for(request)
    capture = CrossrefHttpCapture(200, (), b"{}", NOW, True, None)
    stored = CrossrefStoredCapture(
        "raw:" + "a" * 64,
        claim.claim_id,
        request,
        capture,
        "raw:" + "b" * 64,
        "b" * 64,
    )
    decision = CrossrefRateDecision("accept", None, 0.0, 1.0, None, None, ())
    journal = Journal(request)
    claims = RunnerClaims(claim)
    capture_calls = []

    def capture_claimed(plan, claim):
        capture_calls.append(claim.claim_id)
        return CrossrefClaimedCapture(stored, decision)

    runner = RunClaimedCrossrefHarvestWindow(
        source,
        journal,
        claims,
        SimpleNamespace(load=lambda claim: None),
        capture_claimed,
        Attachments(stored),
        lambda plan, claim: None,
        lambda plan, claim: None,
        lambda plan, request, receipt_id: object(),
        lambda: WorkspaceInfo("workspace:test", 1, True, 17),
        clock=lambda: NOW,
    )

    result = runner(
        plan,
        owner_id="worker:test",
        max_pages=10,
        lease_seconds=60,
    )

    assert result.state == "projection_required"
    assert result.pending_item_count == 1
    assert capture_calls == [claim.claim_id]
    assert journal.saved == 1
    assert journal.commits == 0


def test_claimed_runner_master_off_never_reserves_or_captures_new_http():
    source, plan, request = plan_and_request()
    claim = claim_for(request)
    journal = Journal(request)
    claims = RunnerClaims(claim)

    def no_capture(plan, claim):
        raise AssertionError("master-off must not capture")

    runner = RunClaimedCrossrefHarvestWindow(
        source,
        journal,
        claims,
        SimpleNamespace(load=lambda claim: None),
        no_capture,
        SimpleNamespace(),
        lambda plan, claim: None,
        lambda plan, claim: None,
        lambda plan, request, receipt_id: object(),
        lambda: WorkspaceInfo("workspace:test", 1, False, 17),
        clock=lambda: NOW,
    )

    result = runner(
        plan,
        owner_id="worker:test",
        max_pages=10,
        lease_seconds=60,
    )

    assert result.state == "stop"
    assert result.error_code == "crossref_claim_effects_disabled"
    assert claims.reserve_calls == 0
