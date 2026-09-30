from libs.paper_explanations.domain.services.explanation_verification_rules import (
    ExplanationVerificationRules,
)
from libs.paper_explanations.dtos.explanation_verification import ExplanationVerificationResult
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.ports.support_verifier_port import SupportVerifierPort


class VerifyExplanation:
    def __init__(self, support: SupportVerifierPort) -> None:
        self._support = support

    def __call__(self, draft, claims) -> ExplanationVerificationResult:
        deterministic = ExplanationVerificationRules.deterministic(draft, claims)
        if deterministic.verdict != "passed":
            return ExplanationVerificationResult(deterministic, None, "rejected", "not_run")
        request = ExplanationVerificationRules.support_request(draft, claims, deterministic)
        try:
            candidate = self._support(request)
        except ModelGatewayError:
            return ExplanationVerificationResult(deterministic, None, "pending", "failed")
        result = ExplanationVerificationRules.parse_support(request, candidate)
        verdicts = {item.verdict for item in result.statements}
        qa_state = (
            "rejected"
            if "unsupported" in verdicts
            else "pending"
            if "uncertain" in verdicts
            else "passed"
        )
        return ExplanationVerificationResult(deterministic, result, qa_state, "succeeded")
