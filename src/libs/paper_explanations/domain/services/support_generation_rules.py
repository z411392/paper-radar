from dataclasses import asdict

from libs.paper_explanations.domain.services.generation_json import GenerationJson
from libs.paper_explanations.dtos.explanation_verification import (
    SupportStatementVerdict,
    SupportVerificationCandidate,
    SupportVerificationRequest,
)
from libs.paper_explanations.dtos.structured_generation_request import (
    StructuredGenerationRequest,
)
from libs.paper_explanations.dtos.structured_generation_result import (
    StructuredGenerationResult,
)
from libs.paper_explanations.exceptions.explanation_verification_error import (
    ExplanationVerificationError,
)
from libs.paper_explanations.prompts.support_verification_prompt import (
    SCHEMA_VERSION,
    SYSTEM_PROMPT,
    response_schema,
)


class SupportGenerationRules:
    @classmethod
    def request(
        cls,
        request: SupportVerificationRequest,
    ) -> StructuredGenerationRequest:
        if not isinstance(request, SupportVerificationRequest):
            raise ExplanationVerificationError("invalid_support_generation")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "snapshot_id": request.snapshot_id,
            "input_fingerprint": request.input_fingerprint,
            "statements": [asdict(item) for item in request.statements],
        }
        return StructuredGenerationRequest(
            "support_verification",
            request.input_fingerprint,
            SYSTEM_PROMPT,
            GenerationJson.canonical(payload, "invalid_support_generation"),
            "support_verification",
            response_schema(),
        )

    @staticmethod
    def parse(
        request: SupportVerificationRequest,
        result: StructuredGenerationResult,
    ) -> SupportVerificationCandidate:
        if (
            not isinstance(result, StructuredGenerationResult)
            or result.receipt.finish_reason != "stop"
        ):
            raise ExplanationVerificationError("invalid_support_response")
        try:
            data = GenerationJson.object(
                result.content_json,
                "invalid_support_response",
                limit=262144,
            )
        except Exception as exc:
            raise ExplanationVerificationError("invalid_support_response") from exc
        if (
            set(data)
            != {
                "schema_version",
                "snapshot_id",
                "input_fingerprint",
                "statements",
            }
            or data["schema_version"] != SCHEMA_VERSION
            or data["snapshot_id"] != request.snapshot_id
            or data["input_fingerprint"] != request.input_fingerprint
            or not isinstance(data["statements"], list)
        ):
            raise ExplanationVerificationError("invalid_support_response")
        statements = []
        for row in data["statements"]:
            if (
                not isinstance(row, dict)
                or set(row) != {
                    "statement_index",
                    "verdict",
                    "claim_ids",
                }
                or type(row["statement_index"]) is not int
                or not isinstance(row["verdict"], str)
                or not isinstance(row["claim_ids"], list)
                or any(not isinstance(value, str) for value in row["claim_ids"])
            ):
                raise ExplanationVerificationError("invalid_support_response")
            statements.append(
                SupportStatementVerdict(
                    row["statement_index"],
                    row["verdict"],
                    tuple(row["claim_ids"]),
                )
            )
        return SupportVerificationCandidate(
            request.snapshot_id,
            request.input_fingerprint,
            tuple(statements),
        )
