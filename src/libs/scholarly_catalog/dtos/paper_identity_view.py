from dataclasses import dataclass


@dataclass(frozen=True)
class PaperRevisionView:
    revision_id: str
    native_version: str | None
    content_fingerprint: str
    title: str
    observed_at: str


@dataclass(frozen=True)
class PaperIdentityView:
    work_id: str
    canonical_work_id: str
    manifestation_id: str
    identifier_namespace: str
    normalized_identifier: str
    revisions: tuple[PaperRevisionView, ...]
