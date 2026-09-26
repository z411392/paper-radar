from datetime import datetime
from typing import Protocol

from apps.http.security import LocalHttpSecurityPolicy
from libs.delivery.dtos.reader_feedback import RecordedFeedback


class LocalReadingHistoryReader(Protocol):
    def __call__(
        self,
        work_id: str,
        reader_id: str,
        channel: str | None = None,
    ) -> object: ...


class ReaderFeedbackRecorder(Protocol):
    def __call__(
        self,
        *,
        feedback_id: str,
        reader_id: str,
        work_id: str,
        action: str,
        profile_id: str | None,
        created_at: datetime,
    ) -> RecordedFeedback: ...


class LocalReadingHttpHandlers:
    def __init__(
        self,
        security: LocalHttpSecurityPolicy,
        history: LocalReadingHistoryReader,
        feedback: ReaderFeedbackRecorder,
    ) -> None:
        self._security = security
        self._history = history
        self._feedback = feedback

    def read_history(
        self,
        *,
        host: str,
        work_id: str,
        reader_id: str,
        channel: str | None,
    ) -> object:
        self._security.validate_read(host)
        return self._history(work_id, reader_id, channel)

    def record_feedback(
        self,
        *,
        host: str,
        origin: str,
        mutation_token: str,
        feedback_id: str,
        reader_id: str,
        work_id: str,
        action: str,
        profile_id: str | None,
        created_at: datetime,
    ) -> RecordedFeedback:
        self._security.validate_mutation(
            host=host,
            origin=origin,
            mutation_token=mutation_token,
        )
        return self._feedback(
            feedback_id=feedback_id,
            reader_id=reader_id,
            work_id=work_id,
            action=action,
            profile_id=profile_id,
            created_at=created_at,
        )
