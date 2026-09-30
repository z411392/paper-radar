from dataclasses import dataclass


@dataclass(frozen=True)
class EmailSubscription:
    id: str
    reader_id: str
    channel: str
    enabled: bool
    timezone: str
    local_time: str
    max_items: int
    recipient_ref: str
    policy_version: int
