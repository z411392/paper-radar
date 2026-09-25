from dataclasses import dataclass


@dataclass(frozen=True)
class LocalAccessAssessmentView:
    assessment_id: str
    manifestation_id: str
    location_url: str
    content_scope: str
    reader_access: str
    automated_retrieval: str
    permitted_uses: tuple[str, ...]
    license_id: str | None
    checked_at: str
    expires_at: str | None
    content_version_binding: str | None


@dataclass(frozen=True)
class LocalPaperRevisionView:
    revision_id: str
    manifestation_id: str
    work_id: str
    native_version: str | None
    content_fingerprint: str
    title: str
    source_updated_at: str | None
    published_date: str | None
    date_precision: str | None
    observed_at: str


@dataclass(frozen=True)
class LocalManifestationView:
    manifestation_id: str
    work_id: str
    source_namespace: str
    native_id: str
    manifestation_kind: str
    landing_url: str
    revisions: tuple[LocalPaperRevisionView, ...]
    access_assessments: tuple[LocalAccessAssessmentView, ...]


@dataclass(frozen=True)
class LocalPaperRecord:
    requested_work_id: str
    canonical_work_id: str
    canonical_title: str
    publication_status: str
    first_public_date: str | None
    first_public_precision: str | None
    family_work_ids: tuple[str, ...]
    manifestations: tuple[LocalManifestationView, ...]
