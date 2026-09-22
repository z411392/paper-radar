from dataclasses import dataclass
from datetime import datetime

from libs.discovery.dtos.domain_query_snapshot import DomainQuerySnapshot


@dataclass(frozen=True)
class SourceQueryInput:
    """Workflow-owned mapping must supply exact revisions, never a mutable config file."""

    source_id: str
    profile_id: str
    profile_revision: int
    profile_fingerprint: str
    domain: DomainQuerySnapshot
    window_start: datetime
    window_end: datetime
    profile_sources: tuple[str, ...]
    profile_include: tuple[str, ...] = ()
    profile_exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    free_only: bool = False
    allow_preprints: bool = True
    scope_text: str = ""
    deferred_mode: str = "reject"
    time_basis: str = "submittedDate"
    page_size: int = 200
