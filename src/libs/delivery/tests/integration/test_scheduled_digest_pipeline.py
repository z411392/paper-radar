from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from libs.delivery.application.commands.prepare_scheduled_digest import PrepareScheduledDigest
from libs.delivery.dtos.delivery_queue import QueuedDigest
from libs.delivery.dtos.scheduled_digest import (
    DigestCoverageGap,
    DigestSubscriptionContext,
    ScheduledDigestRequest,
)
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

    def __call__(self, reader_id, revision_id):
        self.calls.append((reader_id, revision_id))
        return self.values.get(revision_id, ())


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
        return frozenset(event_id for event_id in event_ids if event_id in self.notified)


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
    observed_at=NOW - timedelta(hours=1),
):
    return DigestResearchEvent(
        event_id,
        work_id,
        revision_id,
        "new_work",
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


def command(*, events, summaries, relevance, context=None, queue=None):
    queue = queue or FakeQueue()
    usecase = PrepareScheduledDigest(
        events=FakeEvents(tuple(events)),
        summaries=FakeSummaries(summaries),
        relevance=FakeRelevance(relevance),
        context=context or FakeContext(),
        queue=queue,
    )
    return usecase, queue


def test_direct_and_adjacent_domains_make_one_queueable_card_with_coverage_notice():
    summary = DigestCurrentSummary(
        "work:1",
        "revision:1",
        "summary:1",
        ("白話重點一", "白話重點二"),
    )
    usecase, queue = command(
        events=(event(),),
        summaries={("work:1", "revision:1"): summary},
        relevance={
            "revision:1": (
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


def test_same_work_multiple_events_uses_latest_event_identity_only():
    older = event(event_id="event:old", observed_at=NOW - timedelta(hours=2))
    newer = event(event_id="event:new", observed_at=NOW - timedelta(hours=1))
    summary = DigestCurrentSummary("work:1", "revision:1", "summary:1", ("重點",))
    usecase, queue = command(
        events=(newer, older),
        summaries={("work:1", "revision:1"): summary},
        relevance={"revision:1": (DigestRelevance("statistics", "direct"),)},
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "queued"
    assert queue.calls[0][0].items[0].event_id == "event:new"


def test_already_notified_current_event_is_not_queued():
    summary = DigestCurrentSummary("work:1", "revision:1", "summary:1", ("重點",))
    usecase, queue = command(
        events=(event(),),
        summaries={("work:1", "revision:1"): summary},
        relevance={"revision:1": (DigestRelevance("statistics", "direct"),)},
        context=FakeContext(notified=("event:1",)),
    )

    result = usecase(request(), created_at=NOW)

    assert result.state == "empty"
    assert result.item_count == 0
    assert queue.calls == []


def test_missing_current_exact_revision_or_relevance_yields_empty_without_outbox():
    for summaries, relevance in (
        ({}, {"revision:1": (DigestRelevance("statistics", "direct"),)}),
        (
            {
                ("work:1", "revision:1"): DigestCurrentSummary(
                    "work:1",
                    "revision:1",
                    "summary:1",
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
                    (),
                )
            },
            {"revision:1": (DigestRelevance("statistics", "direct"),)},
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
