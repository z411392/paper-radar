from datetime import datetime, timedelta, timezone

import pytest

from libs.delivery.application.commands.prepare_digest import PrepareDigest
from libs.delivery.domain.services.digest_selection_rules import DigestSelectionError
from libs.delivery.dtos.digest_preview import DigestCandidate, PrepareDigestRequest


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def candidate(
    *,
    event_id: str,
    work_id: str,
    summary_id: str,
    revision_id: str,
    current_summary_id: str | None = None,
    current_revision_id: str | None = None,
    qa_state: str = "passed",
    event_at: datetime = NOW,
    priority: int = 10,
    domains: tuple[str, ...] = ("software-engineering",),
    title: str = "A paper",
    source_url: str | None = "https://example.org/paper",
    plain_language: tuple[str, ...] = ("白話重點",),
) -> DigestCandidate:
    return DigestCandidate(
        event_id=event_id,
        work_id=work_id,
        summary_id=summary_id,
        revision_id=revision_id,
        current_summary_id=current_summary_id or summary_id,
        current_revision_id=current_revision_id or revision_id,
        qa_state=qa_state,
        event_at=event_at,
        priority=priority,
        domains=domains,
        title=title,
        source_url=source_url,
        plain_language=plain_language,
    )


def request(*candidates: DigestCandidate, max_items: int = 5, settings_url: str | None = None):
    return PrepareDigestRequest(
        subscription_id="subscription:test",
        period_key="2026-09-23",
        cutoff_at=NOW,
        max_items=max_items,
        candidates=tuple(candidates),
        settings_url=settings_url,
    )


def test_multi_domain_same_event_is_one_item_and_domains_are_merged() -> None:
    command = PrepareDigest()
    first = candidate(
        event_id="event:1",
        work_id="work:1",
        summary_id="summary:1",
        revision_id="revision:1",
        domains=("statistics",),
        priority=20,
    )
    second = candidate(
        event_id="event:1",
        work_id="work:1",
        summary_id="summary:1",
        revision_id="revision:1",
        domains=("machine-learning", "statistics"),
        priority=20,
    )
    other = candidate(
        event_id="event:2",
        work_id="work:2",
        summary_id="summary:2",
        revision_id="revision:2",
        priority=10,
    )

    preview = command(request(second, other, first))

    assert preview.queueable is True
    assert [item.work_id for item in preview.items] == ["work:1", "work:2"]
    assert preview.items[0].domains == ("machine-learning", "statistics")
    assert preview.items[0].event_id == "event:1"


def test_stale_rejected_pending_and_after_cutoff_are_not_selected_or_backfilled() -> None:
    command = PrepareDigest()
    valid = candidate(
        event_id="event:valid",
        work_id="work:valid",
        summary_id="summary:valid",
        revision_id="revision:valid",
    )
    stale = candidate(
        event_id="event:stale",
        work_id="work:stale",
        summary_id="summary:old",
        revision_id="revision:old",
        current_summary_id="summary:new",
        current_revision_id="revision:new",
        priority=100,
    )
    rejected = candidate(
        event_id="event:rejected",
        work_id="work:rejected",
        summary_id="summary:rejected",
        revision_id="revision:rejected",
        qa_state="rejected",
        priority=100,
    )
    pending = candidate(
        event_id="event:pending",
        work_id="work:pending",
        summary_id="summary:pending",
        revision_id="revision:pending",
        qa_state="pending",
        priority=100,
    )
    future = candidate(
        event_id="event:future",
        work_id="work:future",
        summary_id="summary:future",
        revision_id="revision:future",
        event_at=NOW + timedelta(seconds=1),
        priority=100,
    )

    preview = command(request(stale, rejected, pending, future, valid, max_items=5))

    assert [item.work_id for item in preview.items] == ["work:valid"]
    assert len(preview.items) == 1


def test_ranking_is_deterministic_and_max_items_is_a_hard_cap() -> None:
    command = PrepareDigest()
    older = candidate(
        event_id="event:b",
        work_id="work:b",
        summary_id="summary:b",
        revision_id="revision:b",
        event_at=NOW - timedelta(hours=2),
        priority=50,
    )
    newer = candidate(
        event_id="event:a",
        work_id="work:a",
        summary_id="summary:a",
        revision_id="revision:a",
        event_at=NOW - timedelta(hours=1),
        priority=50,
    )
    lower = candidate(
        event_id="event:c",
        work_id="work:c",
        summary_id="summary:c",
        revision_id="revision:c",
        priority=10,
    )

    preview = command(request(lower, older, newer, max_items=2))

    assert [item.event_id for item in preview.items] == ["event:a", "event:b"]


def test_zero_items_returns_non_queueable_empty_preview() -> None:
    preview = PrepareDigest()(
        request(
            candidate(
                event_id="event:pending",
                work_id="work:pending",
                summary_id="summary:pending",
                revision_id="revision:pending",
                qa_state="pending",
            )
        )
    )

    assert preview.queueable is False
    assert preview.items == ()
    assert preview.subject == ""
    assert preview.text_body == ""
    assert preview.html_body == ""


def test_html_is_escaped_and_no_fake_settings_link_is_rendered() -> None:
    preview = PrepareDigest()(
        request(
            candidate(
                event_id="event:1",
                work_id="work:1",
                summary_id="summary:1",
                revision_id="revision:1",
                title="<script>alert(1)</script>",
                plain_language=("<img src=x onerror=alert(1)>",),
                domains=("ml&ai",),
            )
        )
    )

    assert "<script>" not in preview.html_body
    assert "<img" not in preview.html_body
    assert "&lt;script&gt;" in preview.html_body
    assert "&lt;img src=x onerror=alert(1)&gt;" in preview.html_body
    assert "ml&amp;ai" in preview.html_body
    assert "settings" not in preview.html_body.lower()
    assert 'href="#"' not in preview.html_body


def test_valid_settings_url_is_optional_and_rendered_only_when_supplied() -> None:
    preview = PrepareDigest()(
        request(
            candidate(
                event_id="event:1",
                work_id="work:1",
                summary_id="summary:1",
                revision_id="revision:1",
            ),
            settings_url="http://localhost:8787/settings",
        )
    )

    assert 'href="http://localhost:8787/settings"' in preview.html_body
    assert "http://localhost:8787/settings" in preview.text_body


def test_unsafe_source_or_settings_url_is_rejected() -> None:
    command = PrepareDigest()
    with pytest.raises(DigestSelectionError, match="invalid_source_url"):
        command(
            request(
                candidate(
                    event_id="event:1",
                    work_id="work:1",
                    summary_id="summary:1",
                    revision_id="revision:1",
                    source_url="javascript:alert(1)",
                )
            )
        )

    with pytest.raises(DigestSelectionError, match="invalid_settings_url"):
        command(
            request(
                candidate(
                    event_id="event:2",
                    work_id="work:2",
                    summary_id="summary:2",
                    revision_id="revision:2",
                ),
                settings_url="javascript:alert(1)",
            )
        )


def test_conflicting_duplicate_work_identity_is_rejected_instead_of_guessing() -> None:
    command = PrepareDigest()
    first = candidate(
        event_id="event:1",
        work_id="work:1",
        summary_id="summary:1",
        revision_id="revision:1",
        priority=20,
    )
    conflict = candidate(
        event_id="event:other",
        work_id="work:1",
        summary_id="summary:1",
        revision_id="revision:1",
        priority=20,
    )

    with pytest.raises(DigestSelectionError, match="conflicting_work_candidates"):
        command(request(first, conflict))
