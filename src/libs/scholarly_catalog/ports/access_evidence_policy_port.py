from typing import Protocol

from libs.scholarly_catalog.dtos.access_assessment import AccessAssessment
from libs.scholarly_catalog.dtos.access_location_claim import AccessLocationClaim
from libs.scholarly_catalog.dtos.access_location_probe import AccessLocationProbe
from libs.scholarly_catalog.dtos.manifestation_access_identity import ManifestationAccessIdentity


class AccessEvidencePolicyPort(Protocol):
    def evaluate(
        self,
        identity: ManifestationAccessIdentity,
        claim: AccessLocationClaim,
        probe: AccessLocationProbe,
    ) -> AccessAssessment: ...
