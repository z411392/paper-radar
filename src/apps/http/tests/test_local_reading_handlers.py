from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from apps.http.handlers import LocalReadingHttpHandlers
from apps.http.security import LocalHttpBoundaryError, LocalHttpSecurityPolicy
from libs.delivery.dtos.reader_feedback import RecordedFeedback


NOW = datetime(2026, 9, 26, 7, 0, tzinfo=timezone.utc)
TOKEN = "t" * 32


@dataclass(frozen=True)
class HistoryResult:
    work_id: str
    reader_id: str
    channel: str | None


class HistoryQueryStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def __call__(
        self,
        work_id: str,
        reader_id: str,
        channel: str | None = None,
    ) -> HistoryResult:
        self.calls.append((work_id, reader_id, channel))
        return HistoryResult(work_id, reader_id, channel)


class FeedbackCommandStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> RecordedFeedback:
        self.calls.append(kwargs)
        return RecordedFeedback(str(kwargs["feedback_id"]), False)


def _handlers() -> tuple[
    LocalReadingHttpHandlers,
    HistoryQueryStub,
    FeedbackCommandStub,
]:
    history = HistoryQueryStub()
    feedback = FeedbackCommandStub()
    handlers = LocalReadingHttpHandlers(
        LocalHttpSecurityPolicy("127.0.0.1", 8765, TOKEN),
        history,
        feedback,
    )
    return handlers, history, feedback


def test_read_history_calls_existing_inbound_query_after_host_guard() -> None:
    handlers, history, feedback = _handlers()

    result = handlers.read_history(
        host="127.0.0.1:8765",
        work_id="work:one",
        reader_id="reader:local",
        channel="email",
    )

    assert result == HistoryResult("work:one", "reader:local", "email")
    assert history.calls == [("work:one", "reader:local", "email")]
    assert feedback.calls == []


def test_invalid_read_host_fails_before_application_query() -> None:
    handlers, history, _ = _handlers()

    with pytest.raises(LocalHttpBoundaryError, match="invalid_local_host"):
        handlers.read_history(
            host="evil.test",
            work_id="work:one",
            reader_id="reader:local",
            channel=None,
        )

    assert history.calls == []


def test_feedback_calls_record_feedback_only_after_mutation_guard() -> None:
    handlers, _, feedback = _handlers()

    result = handlers.record_feedback(
        host="127.0.0.1:8765",
        origin="http://127.0.0.1:8765",
        mutation_token=TOKEN,
        feedback_id="feedback:one",
        reader_id="reader:local",
        work_id="work:one",
        action="saved",
        profile_id=None,
        created_at=NOW,
    )

    assert result == RecordedFeedback("feedback:one", False)
    assert feedback.calls == [
        {
            "feedback_id": "feedback:one",
            "reader_id": "reader:local",
            "work_id": "work:one",
            "action": "saved",
            "profile_id": None,
            "created_at": NOW,
        }
    ]


def test_invalid_mutation_origin_fails_before_feedback_command() -> None:
    handlers, _, feedback = _handlers()

    with pytest.raises(LocalHttpBoundaryError, match="invalid_mutation_origin"):
        handlers.record_feedback(
            host="127.0.0.1:8765",
            origin="https://evil.test",
            mutation_token=TOKEN,
            feedback_id="feedback:one",
            reader_id="reader:local",
            work_id="work:one",
            action="saved",
            profile_id=None,
            created_at=NOW,
        )

    assert feedback.calls == []
