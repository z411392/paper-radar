from libs.paper_explanations.domain.services.explanation_verification_rules import (
    ExplanationVerificationRules,
)
from libs.paper_explanations.domain.services.support_generation_rules import (
    SupportGenerationRules,
)
from libs.paper_explanations.dtos.explanation_verification import (
    ExplanationVerificationResult,
)
from libs.paper_explanations.dtos.tracked_explanation_verification import (
    TrackedExplanationVerification,
)
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.ports.execute_budgeted_generation_port import (
    ExecuteBudgetedGenerationPort,
)


class VerifyTrackedExplanation:
    def __init__(
        self,
        generation: ExecuteBudgetedGenerationPort,
    ) -> None:
        self._generation = generation

    def __call__(self, draft, claims) -> TrackedExplanationVerification:
        deterministic = ExplanationVerificationRules.deterministic(draft, claims)
        if deterministic.verdict != "passed":
            return TrackedExplanationVerification(
                ExplanationVerificationResult(
                    deterministic,
                    None,
                    "rejected",
                    "not_run",
                ),
                None,
                None,
            )
        support_request = ExplanationVerificationRules.support_request(
            draft,
            claims,
            deterministic,
        )
        try:
            execution = self._generation.execute(
                SupportGenerationRules.request(support_request)
            )
        except ModelGatewayError:
            return TrackedExplanationVerification(
                ExplanationVerificationResult(
                    deterministic,
                    None,
                    "pending",
                    "failed",
                ),
                None,
                None,
            )
        candidate = SupportGenerationRules.parse(
            support_request,
            execution.result,
        )
        support = ExplanationVerificationRules.parse_support(
            support_request,
            candidate,
        )
        verdicts = {item.verdict for item in support.statements}
        qa_state = (
            "rejected"
            if "unsupported" in verdicts
            else "pending"
            if "uncertain" in verdicts
            else "passed"
        )
        return TrackedExplanationVerification(
            ExplanationVerificationResult(
                deterministic,
                support,
                qa_state,
                "succeeded",
            ),
            execution.run_id,
            execution.generation_fingerprint,
        )
