from typing import Protocol

from libs.delivery.dtos.local_reading_history import LocalNotificationView


class LocalDeliveryHistoryStorePort(Protocol):
    def read(
        self,
        reader_id: str,
        work_ids: tuple[str, ...],
        channel: str | None,
    ) -> tuple[LocalNotificationView, ...]: ...
