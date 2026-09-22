from dataclasses import dataclass


@dataclass(frozen=True)
class PaperIdentityResolution:
    work_id: str
    canonical_work_id: str
    manifestation_id: str
    revision_id: str
    identifier_namespace: str
    normalized_identifier: str
    native_version: str | None
    created_work: bool
    created_manifestation: bool
    created_revision: bool
