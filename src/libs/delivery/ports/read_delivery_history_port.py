from typing import Protocol

from libs.delivery.dtos.delivery_history import DeliveryHistory


class ReadDeliveryHistoryPort(Protocol):
    def __call__(
        self,
        reader_id: str,
        *,
        limit: int = 10,
    ) -> tuple[DeliveryHistory, ...]: ...
