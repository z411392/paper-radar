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

        # 60-point email-bot path: deterministic evidence checks are the
        # publication gate. A second LLM judging the first LLM was costly and
        # nondeterministic in live operation, so semantic self-review is not a
        # production prerequisite.
        return TrackedExplanationVerification(
            ExplanationVerificationResult(
                deterministic,
                None,
                "passed",
                "not_run",
            ),
            None,
            None,
        )
