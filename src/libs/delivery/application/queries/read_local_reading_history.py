from libs.delivery.dtos.local_reading_history import LocalReadingHistory
from libs.delivery.ports.local_delivery_history_store_port import (
    LocalDeliveryHistoryStorePort,
)
from libs.scholarly_catalog.ports.read_local_paper_record_port import (
    ReadLocalPaperRecordPort,
)


class ReadLocalReadingHistory:
    def __init__(
        self,
        paper: ReadLocalPaperRecordPort,
        delivery: LocalDeliveryHistoryStorePort,
    ) -> None:
        self._paper = paper
        self._delivery = delivery

    def __call__(
        self,
        work_id: str,
        reader_id: str,
        channel: str | None = None,
    ) -> LocalReadingHistory:
        paper = self._paper(work_id)
        notifications = self._delivery.read(
            reader_id,
            paper.family_work_ids,
            channel,
        )
        return LocalReadingHistory(paper, notifications)
