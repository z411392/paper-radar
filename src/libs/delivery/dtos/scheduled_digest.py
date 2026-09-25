from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DigestCoverageGap:
    kind: str
    identity: str
    reason: str


@dataclass(frozen=True)
class ScheduledDigestRequest:
    subscription_id: str
    period_key: str
    period_start: datetime
    cutoff_at: datetime
    coverage_gaps: tuple[DigestCoverageGap, ...] = ()
    rebuild_reason: str | None = None
    rebuild_outbox_id: str | None = None


@dataclass(frozen=True)
class DigestSubscriptionContext:
    subscription_id: str
    reader_id: str
    channel: str
    enabled: bool
    max_items: int
    workspace_epoch: int


@dataclass(frozen=True)
class ScheduledDigestOutcome:
    state: str
    item_count: int
    digest_id: str | None
    outbox_id: str | None
