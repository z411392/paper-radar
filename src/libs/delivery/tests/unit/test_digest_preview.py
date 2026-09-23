from datetime import datetime, timezone

from libs.delivery.application.commands.prepare_digest import PrepareDigest
from libs.delivery.dtos.digest_preview import DigestCandidate, PrepareDigestRequest


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def item(event_id: str, work_id: str, priority: int) -> DigestCandidate:
    return DigestCandidate(
        event_id=event_id,
        work_id=work_id,
        summary_id=f"summary:{work_id}",
        revision_id=f"revision:{work_id}",
        current_summary_id=f"summary:{work_id}",
        current_revision_id=f"revision:{work_id}",
        qa_state="passed",
        event_at=NOW,
        priority=priority,
        domains=("machine-learning",),
        title=work_id,
        source_url=None,
        plain_language=("重點",),
    )


def request(candidates: tuple[DigestCandidate, ...]) -> PrepareDigestRequest:
    return PrepareDigestRequest(
        subscription_id="subscription:test",
        period_key="2026-09-23",
        cutoff_at=NOW,
        max_items=5,
        candidates=candidates,
    )


def test_input_order_does_not_change_preview_identity() -> None:
    a = item("event:a", "work:a", 2)
    b = item("event:b", "work:b", 1)

    first = PrepareDigest()(request((a, b)))
    second = PrepareDigest()(request((b, a)))

    assert first.items == second.items
    assert first.subject == second.subject
    assert first.text_body == second.text_body
    assert first.html_body == second.html_body
    assert first.content_fingerprint == second.content_fingerprint


def test_dynamic_text_is_not_interpreted_as_html() -> None:
    candidate = DigestCandidate(
        event_id="event:a",
        work_id="work:a",
        summary_id="summary:a",
        revision_id="revision:a",
        current_summary_id="summary:a",
        current_revision_id="revision:a",
        qa_state="passed",
        event_at=NOW,
        priority=1,
        domains=("<b>domain</b>",),
        title='" onclick="alert(1)',
        source_url="https://example.org/?q=%22%3E%3Cscript%3E",
        plain_language=("<svg/onload=alert(1)>",),
    )

    preview = PrepareDigest()(request((candidate,)))

    assert "<svg" not in preview.html_body
    assert "<b>domain</b>" not in preview.html_body
    assert 'onclick="alert(1)' not in preview.html_body
    assert "&lt;svg/onload=alert(1)&gt;" in preview.html_body
    assert "&lt;b&gt;domain&lt;/b&gt;" in preview.html_body
