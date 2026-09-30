from typing import Protocol


class PriorRecipientHistoryPort(Protocol):
    def contains(
        self,
        reader_id: str,
        channel: str,
        work_id: str,
    ) -> bool: ...
