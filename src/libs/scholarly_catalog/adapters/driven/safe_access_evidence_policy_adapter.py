from libs.scholarly_catalog.dtos.access_assessment import AccessAssessment
from libs.scholarly_catalog.dtos.access_location_claim import AccessLocationClaim
from libs.scholarly_catalog.dtos.access_location_probe import AccessLocationProbe
from libs.scholarly_catalog.dtos.manifestation_access_identity import ManifestationAccessIdentity
from libs.scholarly_catalog.exceptions.paper_identity_error import PaperIdentityError


class SafeAccessEvidencePolicyAdapter:
    def __init__(self, allowed_evidence_sources: frozenset[str]) -> None:
        self._allowed_evidence_sources = allowed_evidence_sources

    def evaluate(
        self,
        identity: ManifestationAccessIdentity,
        claim: AccessLocationClaim,
        probe: AccessLocationProbe,
    ) -> AccessAssessment:
        raise PaperIdentityError("not_implemented")
