import json
from datetime import datetime, timezone

import pytest

from libs.delivery.application.commands.prepare_digest import PrepareDigest
from libs.delivery.domain.services.digest_artifact_rules import DigestArtifactRules
from libs.delivery.domain.services.digest_selection_rules import DigestSelectionError
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


def test_coverage_notes_are_escaped_rendered_and_part_of_preview_identity() -> None:
    candidate = item("event:a", "work:a", 1)
    base = request((candidate,))
    first = PrepareDigest()(base)
    with_gap = PrepareDigest()(
        PrepareDigestRequest(
            subscription_id=base.subscription_id,
            period_key=base.period_key,
            cutoff_at=base.cutoff_at,
            max_items=base.max_items,
            candidates=base.candidates,
            coverage_notes=("arXiv 暫時失敗 <retry>",),
        )
    )

    assert "資料覆蓋提醒" in with_gap.text_body
    assert "arXiv 暫時失敗 <retry>" in with_gap.text_body
    assert "&lt;retry&gt;" in with_gap.html_body
    assert "<retry>" not in with_gap.html_body
    assert first.content_fingerprint != with_gap.content_fingerprint


def status_item(event_id: str, event_kind: str, priority: int = 100) -> DigestCandidate:
    return DigestCandidate(
        event_id=event_id,
        work_id="work:status",
        summary_id=None,
        revision_id=None,
        current_summary_id=None,
        current_revision_id=None,
        qa_state="not_applicable",
        event_at=NOW,
        priority=priority,
        domains=(),
        title="Paper with status change",
        source_url="https://example.org/status",
        plain_language=("來源目前回報這篇研究有狀態更新。",),
        item_kind="status_notice",
        event_kind=event_kind,
    )


def test_status_notices_keep_distinct_event_identity_for_same_work() -> None:
    correction = status_item("event:correction", "correction")
    retraction = status_item("event:retraction", "retraction")

    preview = PrepareDigest()(request((retraction, correction)))

    assert [item.event_id for item in preview.items] == [
        "event:correction",
        "event:retraction",
    ]
    assert all(item.item_kind == "status_notice" for item in preview.items)
    assert {item.event_kind for item in preview.items} == {"correction", "retraction"}
    assert all(item.summary_id is None for item in preview.items)
    assert "研究狀態更新" in preview.subject


def test_status_notice_is_prioritized_over_regular_paper() -> None:
    paper = item("event:paper", "work:paper", 20)
    status = status_item("event:status", "correction", priority=100)

    preview = PrepareDigest()(request((paper, status)))

    assert [entry.item_kind for entry in preview.items] == ["status_notice", "paper"]


def test_status_candidate_shape_cannot_be_forged_as_regular_event() -> None:
    forged = status_item("event:status", "new_work")

    with pytest.raises(DigestSelectionError, match="invalid_event_kind"):
        PrepareDigest()(request((forged,)))


def test_status_artifact_is_v2_and_keeps_notice_identity() -> None:
    preview = PrepareDigest()(request((status_item("event:status", "correction"),)))

    content = DigestArtifactRules.serialize(preview)
    payload = json.loads(content)

    assert not {
        "reader_id",
        "recipient",
        "recipient_ref",
        "email",
        "smtp_password",
        "api_key",
    } & payload.keys()
    assert payload["schema_version"] == 2
    assert payload["items"] == [
        {
            "position": 1,
            "event_id": "event:status",
            "work_id": "work:status",
            "summary_id": None,
            "revision_id": None,
            "item_kind": "status_notice",
            "event_kind": "correction",
        }
    ]
    parsed = DigestArtifactRules.parse(content)
    assert parsed.content_fingerprint == preview.content_fingerprint


def test_v1_digest_artifact_remains_readable() -> None:
    content = json.dumps(
        {
            "schema_version": 1,
            "subscription_id": "subscription:test",
            "period_key": "2026-09-23",
            "cutoff_at": NOW.isoformat(),
            "content_fingerprint": "a" * 64,
            "subject": "subject",
            "text_body": "text",
            "html_body": "<p>html</p>",
            "items": [
                {
                    "position": 1,
                    "event_id": "event:old",
                    "work_id": "work:old",
                    "summary_id": "summary:old",
                    "revision_id": "revision:old",
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    parsed = DigestArtifactRules.parse(content)

    assert parsed.subscription_id == "subscription:test"
    assert parsed.period_key == "2026-09-23"
    assert parsed.content_fingerprint == "a" * 64
