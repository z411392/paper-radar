from datetime import datetime
from typing import Protocol

from libs.scholarly_catalog.dtos.access_assessment import AccessAssessment
from libs.scholarly_catalog.dtos.manifestation_access_identity import ManifestationAccessIdentity


class AccessAssessmentStorePort(Protocol):
    def read_manifestation(self, manifestation_id: str) -> ManifestationAccessIdentity: ...

    def save(self, assessment: AccessAssessment) -> AccessAssessment: ...

    def read_current(self, manifestation_id: str, at: datetime) -> AccessAssessment: ...
