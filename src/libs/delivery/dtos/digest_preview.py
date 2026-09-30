from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DigestCandidate:
    event_id: str
    work_id: str
    summary_id: str | None
    revision_id: str | None
    current_summary_id: str | None
    current_revision_id: str | None
    qa_state: str
    event_at: datetime
    priority: int
    domains: tuple[str, ...]
    title: str
    source_url: str | None
    plain_language: tuple[str, ...]
    item_kind: str = "paper"
    event_kind: str = "new_work"


@dataclass(frozen=True)
class SelectedDigestItem:
    event_id: str
    work_id: str
    summary_id: str | None
    revision_id: str | None
    event_at: datetime
    priority: int
    domains: tuple[str, ...]
    title: str
    source_url: str | None
    plain_language: tuple[str, ...]
    item_kind: str = "paper"
    event_kind: str = "new_work"


@dataclass(frozen=True)
class PrepareDigestRequest:
    subscription_id: str
    period_key: str
    cutoff_at: datetime
    max_items: int
    candidates: tuple[DigestCandidate, ...]
    settings_url: str | None = None
    coverage_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DigestPreview:
    subscription_id: str
    period_key: str
    cutoff_at: datetime
    queueable: bool
    items: tuple[SelectedDigestItem, ...]
    subject: str
    text_body: str
    html_body: str
    content_fingerprint: str | None
