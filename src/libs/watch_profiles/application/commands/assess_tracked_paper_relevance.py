from libs.paper_explanations.dtos.structured_generation_request import (
    StructuredGenerationRequest,
)
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.ports.execute_budgeted_generation_port import (
    ExecuteBudgetedGenerationPort,
)
from libs.watch_profiles.domain.services.relevance_rules import RelevanceRules
from libs.watch_profiles.dtos.relevance_assessment import RelevanceCandidate
from libs.watch_profiles.dtos.tracked_relevance_assessment import (
    TrackedRelevanceAssessment,
)
from libs.watch_profiles.exceptions.relevance_assessment_error import (
    RelevanceAssessmentError,
)
from libs.watch_profiles.ports.read_domain_definition_port import (
    ReadDomainDefinitionPort,
)
from libs.watch_profiles.ports.read_watch_profile_port import ReadWatchProfilePort


class AssessTrackedPaperRelevance:
    def __init__(
        self,
        profiles: ReadWatchProfilePort,
        domains: ReadDomainDefinitionPort,
        generation: ExecuteBudgetedGenerationPort,
    ) -> None:
        self._profiles = profiles
        self._domains = domains
        self._generation = generation

    def __call__(self, profile_id, domain_id, claims) -> TrackedRelevanceAssessment:
        profile = self._profiles(profile_id)
        revision = RelevanceRules.profile(profile_id, domain_id, profile)
        request = RelevanceRules.request(
            profile,
            self._domains(domain_id, revision),
            claims,
        )
        structured = StructuredGenerationRequest(
            "relevance_assessment",
            request.input_fingerprint,
            request.system_prompt,
            request.payload_json,
            "paper_relevance",
            request.response_schema_json,
            request.model_name,
        )
        try:
            execution = self._generation.execute(structured)
        except ModelGatewayError as exc:
            current = self._profiles(profile_id)
            state = "stale" if current != profile else "failed"
            code = "profile_changed" if state == "stale" else exc.code
            return TrackedRelevanceAssessment(
                RelevanceRules.failure(request, state, code),
                exc.run_id,
                exc.generation_fingerprint,
            )
        if self._profiles(profile_id) != profile:
            return TrackedRelevanceAssessment(
                RelevanceRules.failure(request, "stale", "profile_changed"),
                execution.run_id,
                execution.generation_fingerprint,
            )
        finish = execution.result.receipt.finish_reason
        candidate = RelevanceCandidate(
            request.model_name,
            "" if finish is None else finish,
            execution.result.content_json,
        )
        try:
            assessment = RelevanceRules.parse(request, candidate, claims)
        except RelevanceAssessmentError as exc:
            assessment = RelevanceRules.failure(request, "failed", exc.code)
        return TrackedRelevanceAssessment(
            assessment,
            execution.run_id,
            execution.generation_fingerprint,
        )
