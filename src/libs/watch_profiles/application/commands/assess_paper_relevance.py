from libs.paper_explanations.dtos.claim_extraction_result import ClaimExtractionResult
from libs.watch_profiles.domain.services.relevance_rules import RelevanceRules
from libs.watch_profiles.dtos.relevance_assessment import RelevanceAssessment
from libs.watch_profiles.exceptions.relevance_assessment_error import (
    RelevanceAssessmentError,
    RelevanceModelError,
)
from libs.watch_profiles.ports.read_domain_definition_port import ReadDomainDefinitionPort
from libs.watch_profiles.ports.read_watch_profile_port import ReadWatchProfilePort
from libs.watch_profiles.ports.relevance_candidate_port import RelevanceCandidatePort


class AssessPaperRelevance:
    def __init__(
        self, profiles: ReadWatchProfilePort, domains: ReadDomainDefinitionPort,
        model: RelevanceCandidatePort,
    ) -> None:
        self._profiles = profiles
        self._domains = domains
        self._model = model

    def __call__(
        self, profile_id: str, domain_id: str, claims: ClaimExtractionResult
    ) -> RelevanceAssessment:
        profile = self._profiles(profile_id)
        revision = RelevanceRules.profile(profile_id, domain_id, profile)
        request = RelevanceRules.request(profile, self._domains(domain_id, revision), claims)
        try:
            candidate = self._model(request)
        except RelevanceModelError as exc:
            if self._profiles(profile_id) != profile:
                return RelevanceRules.failure(request, "stale", "profile_changed")
            return RelevanceRules.failure(request, "failed", exc.code)
        if self._profiles(profile_id) != profile:
            return RelevanceRules.failure(request, "stale", "profile_changed")
        try:
            return RelevanceRules.parse(request, candidate, claims)
        except RelevanceAssessmentError as exc:
            return RelevanceRules.failure(request, "failed", exc.code)
