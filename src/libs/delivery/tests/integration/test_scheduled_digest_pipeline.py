from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from libs.delivery.application.commands.prepare_scheduled_digest import PrepareScheduledDigest
from libs.delivery.dtos.delivery_queue import QueuedDigest
from libs.delivery.dtos.scheduled_digest import (
    DigestCoverageGap,
    DigestSubscriptionContext,
    ScheduledDigestRequest,
)
from libs.delivery.exceptions.scheduled_digest_error import ScheduledDigestError
from libs.paper_explanations.dtos.digest_current_summary import DigestCurrentSummary
from libs.scholarly_catalog.dtos.digest_research_event import DigestResearchEvent
from libs.watch_profiles.dtos.digest_relevance import DigestRelevance


NOW = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)
START = NOW - timedelta(days=1)


@dataclass
class FakeEvents:
    values: tuple[DigestResearchEvent, ...]
    calls: int = 0

    def __call__(self, period_start, cutoff_at):
        self.calls += 1
        assert period_start == START
        assert cutoff_at == NOW
        return self.values


class FakeSummaries:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def __call__(self, work_id, revision_id):
        self.calls.append((work_id, revision_id))
        return self.values.get((work_id, revision_id))


class FakeRelevance:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def __call__(self, reader_id, revision_id, snapshot_id):
        self.calls.append((reader_id, revision_id, snapshot_id))
        return self.values.get((revision_id, snapshot_id), ())


class FakeContext:
    def __init__(self, *, enabled=True, notified=()):
        self.value = DigestSubscriptionContext(
            "subscription:daily",
            "reader:local",
            "email",
            enabled,
            5,
            9,
        )
        self.notified = frozenset(notified)
        self.notified_calls = []

    def load(self, subscription_id):
        assert subscription_id == self.value.subscription_id
        return self.value

    def already_notified(self, reader_id, channel, event_ids):
        self.notified_calls.append((reader_id, channel, event_ids))
        return frozenset(
            event_id for event_id in event_ids if event_id in self.notified
        )

    def already_notified_for_rebuild(
        self,
        reader_id,
        channel,
        event_ids,
        outbox_id,
    ):
        self.notified_calls.append(
            (reader_id, channel, event_ids, outbox_id)
        )
        return frozenset(
            event_id for event_id in event_ids if event_id in self.notified
        )


class FakePriorRecipient:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def contains(self, reader_id, channel, work_id):
        self.calls.append((reader_id, channel, work_id))
        return self.allowed


class FakeQueue:
    def __init__(self):
        self.calls = []

    def __call__(
        self,
        preview,
        *,
        reader_id,
        channel,
        workspace_epoch,
        created_at,
    ):
        self.calls.append(
            (preview, reader_id, channel, workspace_epoch, created_at)
        )
        return QueuedDigest(
            "digest-record:" + "a" * 64,
            "outbox:" + "b" * 64,
            "delivery-request:" + "c" * 64,
            "d" * 64,
            False,
        )


def event(
    *,
    event_id="event:1",
    work_id="work:1",
    revision_id="revision:1",
    event_kind="new_work",
    observed_at=NOW - timedelta(hours=1),
):
    return DigestResearchEvent(
        event_id,
        work_id,
        revision_id,
        event_kind,
        observed_at,
        "Paper",
        "https://example.org/paper",
    )


def status_event(
    event_id,
    event_kind,
    *,
    work_id="work:1",
    observed_at=NOW - timedelta(minutes=30),
):
    return DigestResearchEvent(
        event_id,
        work_id,
        None,
        event_kind,
        observed_at,
        "Paper",
        "https://example.org/paper",
    )


def request(*, gaps=()):
    return ScheduledDigestRequest(
        "subscription:daily",
        "2026-09-24",
        START,
        NOW,
        gaps,
    )


def command(
    *,
    events,
    summaries,
    relevance,
    context=None,
    queue=None,
    prior_recipient=None,
):
    queue = queue or FakeQueue()
    usecase = PrepareScheduledDigest(
        events=FakeEvents(tuple(events)),
        summaries=FakeSummaries(summaries),
        relevance=FakeRelevance(relevance),
        context=context or FakeContext(),
        queue=queue,
        prior_recipient=prior_recipient,
    )
    return usecase, queue


def test_direct_and_adjacent_domains_make_one_queueable_card_with_coverage_notice():
    summary = DigestCurrentSummary(
        "work:1",
        "revision:1",
        "summary:1",
        "snapshot:1",
        ("白話重點一", "白話重點二"),
    )
    usecase, queue = command(
        events=(event(),),
        summaries={("work:1", "revision:1"): summary},
        relevance={
            ("revision:1", "snapshot:1"): (
                DigestRelevance("statistics", "adjacent"),
                DigestRelevance("machine_learning", "direct"),
            )
        },
    )

    result = usecase(
        request(
            gaps=(
                DigestCoverageGap(
                    "harvest",
                    "badminton:pubmed",
                    "source_scheduler_not_supported",
                ),
            )
        ),
        created_at=NOW + timedelta(minutes=2),
    )

    assert result.state == "queued"
    assert result.item_count == 1
    assert len(queue.calls) == 1
    preview = queue.calls[0][0]
    assert preview.items[0].domains == ("machine_learning", "statistics")
    assert preview.items[0].plain_language == ("白話重點一", "白話重點二")
    assert "資料覆蓋提醒" in preview.text_body
    assert "badminton:pubmed" in preview.text_body
    assert queue.calls[0][1:4] == ("reader:local", "email", 9)


def test_revision_event_is_rendered_as_update_in_queued_digest():
    revision = event(
        event_id="event:revision",
        event_kind="revision_available",
    )
    summary = DigestCurrentSummary(
        "work:1",
        "revision:1",
        "summary:1",
        "snapshot:1",
        ("這是舊論文的最新版本摘要。",),
    )
    usecase, queue = command(
        events=(revision,),
        summaries={("work:1", "revision:1"): summary},
        relevance={
            ("revision:1", "snapshot:1"): (
                DigestRelevance("machine_learning", "direct"),
            )
        },
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "queued"
    preview = queue.calls[0][0]
    assert preview.items[0].event_kind == "revision_available"
    assert preview.subject == "Paper Radar｜論文更新 1 篇"
    assert "1. [更新] Paper" in preview.text_body
    assert "類型：論文更新" in preview.text_body
    assert "[更新] Paper" in preview.html_body


def test_same_work_multiple_events_uses_latest_event_identity_only():
    older = event(event_id="event:old", observed_at=NOW - timedelta(hours=2))
    newer = event(event_id="event:new", observed_at=NOW - timedelta(hours=1))
    summary = DigestCurrentSummary(
        "work:1",
        "revision:1",
        "summary:1",
        "snapshot:1",
        ("重點",),
    )
    usecase, queue = command(
        events=(newer, older),
        summaries={("work:1", "revision:1"): summary},
        relevance={("revision:1", "snapshot:1"): (DigestRelevance("statistics", "direct"),)},
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "queued"
    assert queue.calls[0][0].items[0].event_id == "event:new"


def test_already_notified_current_event_is_not_queued():
    summary = DigestCurrentSummary(
        "work:1",
        "revision:1",
        "summary:1",
        "snapshot:1",
        ("重點",),
    )
    usecase, queue = command(
        events=(event(),),
        summaries={("work:1", "revision:1"): summary},
        relevance={("revision:1", "snapshot:1"): (DigestRelevance("statistics", "direct"),)},
        context=FakeContext(notified=("event:1",)),
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "empty"
    assert result.item_count == 0
    assert queue.calls == []


def test_missing_current_exact_revision_or_relevance_yields_empty_without_outbox():
    for summaries, relevance in (
        ({}, {("revision:1", "snapshot:1"): (DigestRelevance("statistics", "direct"),)}),
        (
            {
                ("work:1", "revision:1"): DigestCurrentSummary(
                    "work:1",
                    "revision:1",
                    "summary:1",
                    "snapshot:1",
                    ("重點",),
                )
            },
            {},
        ),
        (
            {
                ("work:1", "revision:1"): DigestCurrentSummary(
                    "work:1",
                    "revision:1",
                    "summary:1",
                    "snapshot:1",
                    (),
                )
            },
            {("revision:1", "snapshot:1"): (DigestRelevance("statistics", "direct"),)},
        ),
    ):
        usecase, queue = command(
            events=(event(),),
            summaries=summaries,
            relevance=relevance,
        )

        result = usecase(request(), created_at=NOW)

        assert result.state == "empty"
        assert queue.calls == []


def test_disabled_subscription_stops_before_reading_period_events():
    events = FakeEvents((event(),))
    context = FakeContext(enabled=False)
    queue = FakeQueue()
    usecase = PrepareScheduledDigest(
        events=events,
        summaries=FakeSummaries({}),
        relevance=FakeRelevance({}),
        context=context,
        queue=queue,
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "disabled"
    assert events.calls == 0
    assert queue.calls == []


def test_relevance_from_another_snapshot_does_not_qualify_current_summary():
    summary = DigestCurrentSummary(
        "work:1",
        "revision:1",
        "summary:1",
        "snapshot:current",
        ("重點",),
    )
    usecase, queue = command(
        events=(event(),),
        summaries={("work:1", "revision:1"): summary},
        relevance={
            ("revision:1", "snapshot:old"): (
                DigestRelevance("statistics", "direct"),
            )
        },
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "empty"
    assert queue.calls == []


def test_status_notice_skips_summary_and_relevance_but_requires_prior_recipient():
    prior = FakePriorRecipient(True)
    usecase, queue = command(
        events=(status_event("event:correction", "correction"),),
        summaries={},
        relevance={},
        prior_recipient=prior,
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "queued"
    assert result.item_count == 1
    preview = queue.calls[0][0]
    assert preview.items[0].item_kind == "status_notice"
    assert preview.items[0].event_kind == "correction"
    assert preview.items[0].summary_id is None
    assert preview.items[0].revision_id is None
    assert "來源目前回報這篇研究有更正紀錄" in preview.text_body
    assert prior.calls == [("reader:local", "email", "work:1")]


def test_same_work_correction_and_retraction_are_distinct_status_items():
    prior = FakePriorRecipient(True)
    usecase, queue = command(
        events=(
            status_event("event:correction", "correction", observed_at=NOW - timedelta(hours=2)),
            status_event("event:retraction", "retraction", observed_at=NOW - timedelta(hours=1)),
        ),
        summaries={},
        relevance={},
        prior_recipient=prior,
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "queued"
    assert [item.event_id for item in queue.calls[0][0].items] == [
        "event:retraction",
        "event:correction",
    ]
    assert "不代表 Paper Radar 自行判定研究結論錯誤" in queue.calls[0][0].text_body


def test_status_notice_is_not_queued_for_non_prior_recipient():
    usecase, queue = command(
        events=(status_event("event:correction", "correction"),),
        summaries={},
        relevance={},
        prior_recipient=FakePriorRecipient(False),
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "empty"
    assert queue.calls == []


def test_status_notice_without_prior_recipient_port_fails_closed():
    usecase, _ = command(
        events=(status_event("event:correction", "correction"),),
        summaries={},
        relevance={},
    )

    with pytest.raises(ScheduledDigestError, match="prior_recipient_check_unavailable"):
        usecase(request(), created_at=NOW)


def test_rebuild_uses_same_outbox_ledger_scope_and_passes_rebuild_authority():
    prior = FakePriorRecipient(True)
    queue = FakeQueue()
    usecase, _ = command(
        events=(status_event("event:correction", "correction"),),
        summaries={},
        relevance={},
        prior_recipient=prior,
        queue=queue,
        context=FakeContext(),
    )
    rebuild = ScheduledDigestRequest(
        "subscription:daily",
        "2026-09-24",
        START,
        NOW,
        (),
        "current_input_stale",
        "outbox:stale",
    )

    result = usecase(rebuild, created_at=NOW)

    assert result.state == "queued"
    context = usecase._context
    assert context.notified_calls == [
        (
            "reader:local",
            "email",
            ("event:correction",),
            "outbox:stale",
        )
    ]
    assert queue.calls[0][0].items[0].event_id == "event:correction"
