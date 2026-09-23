from libs.paper_explanations.exceptions.explanation_verification_error import (
    ExplanationVerificationError,
)


class ExplanationVerificationRules:
    @classmethod
    def deterministic(cls, draft, claims):
        raise ExplanationVerificationError("not_implemented")

    @classmethod
    def support_request(cls, draft, claims, report):
        raise ExplanationVerificationError("not_implemented")

    @classmethod
    def parse_support(cls, request, candidate):
        raise ExplanationVerificationError("not_implemented")
