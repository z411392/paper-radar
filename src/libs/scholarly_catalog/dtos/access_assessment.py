from dataclasses import dataclass


@dataclass(frozen=True)
class AccessAssessment:
    assessment_id: str
    manifestation_id: str
    location_url: str
    content_scope: str
    reader_access: str
    automated_retrieval: str
    permitted_uses: tuple[str, ...]
    license_id: str | None
    evidence_json: str
    checked_at: str
    expires_at: str | None
    content_version_binding: str | None
