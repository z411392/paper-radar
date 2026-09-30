import pytest

from libs.delivery.adapters.driven.sqlite_delivery_history_adapter import (
    SqliteDeliveryHistoryAdapter,
)
from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError


@pytest.mark.parametrize(
    "outbox_state,digest_state,expected",
    [
        ("pending", "queued", "pending"),
        ("sending", "queued", "sending"),
        ("provider_accepted", "sent", "sent"),
        ("failed", "queued", "failed"),
        ("unknown", "unknown", "unknown"),
        ("cancelled", "cancelled", "cancelled"),
    ],
)
def test_delivery_history_maps_durable_outbox_state(
    outbox_state: str,
    digest_state: str,
    expected: str,
) -> None:
    assert (
        SqliteDeliveryHistoryAdapter._delivery_state(
            outbox_state,
            digest_state,
        )
        == expected
    )


@pytest.mark.parametrize(
    "outbox_state,notification_state,expected",
    [
        ("pending", "reserved", "pending"),
        ("sending", "reserved", "sending"),
        ("provider_accepted", "accepted", "sent"),
        ("failed", "reserved", "failed"),
        ("unknown", "unknown", "unknown"),
        ("cancelled", "cancelled", "cancelled"),
    ],
)
def test_delivery_history_maps_per_item_send_status(
    outbox_state: str,
    notification_state: str,
    expected: str,
) -> None:
    assert (
        SqliteDeliveryHistoryAdapter._send_status(
            outbox_state,
            notification_state,
        )
        == expected
    )


@pytest.mark.parametrize(
    "outbox_state,other_state",
    [
        ("provider_accepted", "reserved"),
        ("pending", "accepted"),
        ("unknown", "reserved"),
        ("failed", "accepted"),
    ],
)
def test_delivery_history_rejects_inconsistent_item_state(
    outbox_state: str,
    other_state: str,
) -> None:
    with pytest.raises(
        DeliveryStoreError,
        match="delivery_history_corrupt",
    ):
        SqliteDeliveryHistoryAdapter._send_status(
            outbox_state,
            other_state,
        )
