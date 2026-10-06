from libs.paper_explanations.dtos.model_http_response import ModelHttpResponse
from libs.paper_explanations.dtos.structured_generation_result import (
    GenerationReceipt,
    StructuredGenerationResult,
)
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError


PRIVATE = "PRIVATE-NOTE-AND-CREDENTIAL-SENTINEL"


def receipt() -> GenerationReceipt:
    return GenerationReceipt(
        input_fingerprint="a" * 64,
        request_sha256="b" * 64,
        generation_id="gen:safe",
        requested_model="google/gemini-3.8-flash",
        returned_model="google/gemini-3.8-flash",
        provider="provider",
        input_tokens=10,
        output_tokens=5,
        cost_usd="0.0001",
        finish_reason="stop",
    )


def test_common_diagnostics_do_not_render_model_or_provider_content() -> None:
    result = StructuredGenerationResult(
        content_json='{"private":"' + PRIVATE + '"}',
        receipt=receipt(),
    )
    response = ModelHttpResponse(500, PRIVATE.encode())
    error = ModelGatewayError("provider_unavailable", receipt())

    diagnostic = "\n".join(
        (
            repr(result),
            repr(response),
            repr(error),
            str(error),
            repr(error.receipt),
        )
    )

    assert PRIVATE not in diagnostic
    assert "provider_unavailable" in diagnostic
