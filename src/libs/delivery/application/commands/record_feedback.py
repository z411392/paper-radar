from datetime import datetime

from libs.delivery.dtos.reader_feedback import ReaderFeedbackRequest, RecordedFeedback
from libs.delivery.ports.reader_feedback_store_port import ReaderFeedbackStorePort


class RecordFeedback:
    def __init__(self, store: ReaderFeedbackStorePort) -> None:
        self._store = store

    def __call__(
        self,
        *,
        feedback_id: str,
        reader_id: str,
        work_id: str,
        action: str,
        profile_id: str | None,
        created_at: datetime,
    ) -> RecordedFeedback:
        return self._store.record_feedback(
            ReaderFeedbackRequest(
                feedback_id=feedback_id,
                reader_id=reader_id,
                work_id=work_id,
                action=action,
                profile_id=profile_id,
                created_at=created_at,
            )
        )
