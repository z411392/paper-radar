import json
import os
from decimal import Decimal

import pytest

from apps.cli.model_commissioning import COMMISSIONED_OPENROUTER_POLICY
from libs.paper_explanations.adapters.driven.https_openrouter_transport import (
    HttpsOpenRouterTransport,
)
from libs.paper_explanations.adapters.driven.openrouter_structured_adapter import (
    OpenRouterStructuredAdapter,
)
from libs.paper_explanations.dtos.structured_generation_request import (
    MODEL_NAME,
    StructuredGenerationRequest,
)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise AssertionError(f"missing live OpenRouter env: {name}")
    return value


@pytest.mark.live_external
@pytest.mark.skipif(
    os.environ.get("PAPER_RADAR_LIVE_OPENROUTER") != "1",
    reason="set PAPER_RADAR_LIVE_OPENROUTER=1 for the bounded OpenRouter smoke",
)
def test_live_openrouter_returns_strict_structured_receipt() -> None:
    api_key = _required("PAPER_RADAR_OPENROUTER_API_KEY")
    transport = HttpsOpenRouterTransport(
        COMMISSIONED_OPENROUTER_POLICY,
        credential=lambda: api_key,
    )
    adapter = OpenRouterStructuredAdapter(
        transport,
        COMMISSIONED_OPENROUTER_POLICY,
    )
    request = StructuredGenerationRequest(
        task_kind="live_smoke",
        input_fingerprint="9" * 64,
        system_prompt=(
            "Return only JSON matching the supplied schema. "
            "Set ok to true and echo exactly to paper-radar-live."
        ),
        payload_json='{"echo":"paper-radar-live"}',
        schema_name="paper_radar_live_smoke",
        response_schema_json=(
            '{"type":"object","properties":{'
            '"ok":{"type":"boolean"},'
            '"echo":{"type":"string"}'
            '},"required":["ok","echo"],"additionalProperties":false}'
        ),
        model_name=MODEL_NAME,
    )

    result = adapter(request)
    payload = json.loads(result.content_json)

    assert payload == {
        "ok": True,
        "echo": "paper-radar-live",
    }
    receipt = result.receipt
    assert receipt.requested_model == MODEL_NAME
    assert receipt.returned_model == MODEL_NAME
    assert receipt.generation_id is not None
    assert receipt.finish_reason == "stop"
    assert receipt.input_tokens is not None and receipt.input_tokens > 0
    assert receipt.output_tokens is not None and receipt.output_tokens > 0
    assert receipt.cost_usd is not None
    assert Decimal(receipt.cost_usd) > 0
    assert len(receipt.request_sha256) == 64
