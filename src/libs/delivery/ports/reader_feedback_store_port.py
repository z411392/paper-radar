from typing import Protocol

from libs.delivery.dtos.reader_feedback import ReaderFeedbackRequest, RecordedFeedback


class ReaderFeedbackStorePort(Protocol):
    def record_feedback(self, request: ReaderFeedbackRequest) -> RecordedFeedback: ...
