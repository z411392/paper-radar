from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ConfigureDeliverySubscriptionRequest:
    reader_id: str
    timezone: str
    local_time: str
    max_items: int
    recipient_ref: str
    enabled: bool


@dataclass(frozen=True)
class DeliverySubscription:
    subscription_id: str
    reader_id: str
    channel: str
    enabled: bool
    timezone: str
    local_time: str
    max_items: int
    recipient_ref: str
    policy_version: int
    created_at: datetime
    replayed: bool
