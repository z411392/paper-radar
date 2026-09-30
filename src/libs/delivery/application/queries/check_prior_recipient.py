from libs.delivery.ports.prior_recipient_history_port import (
    PriorRecipientHistoryPort,
)


class CheckPriorRecipient:
    def __init__(self, history: PriorRecipientHistoryPort) -> None:
        self._history = history

    def __call__(
        self,
        reader_id: str,
        channel: str,
        work_id: str,
    ) -> bool:
        return self._history.contains(reader_id, channel, work_id)
