from typing import Protocol

from libs.delivery.dtos.local_reading_history import LocalReadingHistory


class ReadLocalReadingHistoryPort(Protocol):
    def __call__(
        self,
        work_id: str,
        reader_id: str,
        channel: str | None = None,
    ) -> LocalReadingHistory: ...
