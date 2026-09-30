from libs.scholarly_catalog.dtos.access_assessment import AccessAssessment
from libs.scholarly_catalog.dtos.access_location_claim import AccessLocationClaim
from libs.scholarly_catalog.dtos.access_location_probe import AccessLocationProbe
from libs.scholarly_catalog.exceptions.access_assessment_error import AccessAssessmentError
from libs.scholarly_catalog.ports.access_assessment_store_port import AccessAssessmentStorePort
from libs.scholarly_catalog.ports.access_evidence_policy_port import AccessEvidencePolicyPort


class VerifyReadableLocation:
    def __init__(
        self,
        policy: AccessEvidencePolicyPort,
        store: AccessAssessmentStorePort,
    ) -> None:
        self._policy = policy
        self._store = store

    def __call__(
        self,
        claim: AccessLocationClaim,
        probe: AccessLocationProbe,
    ) -> AccessAssessment:
        if not isinstance(claim, AccessLocationClaim) or not isinstance(probe, AccessLocationProbe):
            raise AccessAssessmentError("invalid_access_evidence")
        identity = self._store.read_manifestation(claim.manifestation_id)
        assessment = self._policy.evaluate(identity, claim, probe)
        return self._store.save(assessment)
