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
from libs.paper_explanations.domain.services.claim_extraction_rules import (
    ClaimExtractionRules,
)
from libs.paper_explanations.dtos.claim_candidate import ClaimCandidate
from libs.paper_explanations.dtos.structured_generation_request import (
    MODEL_NAME,
    StructuredGenerationRequest,
)
from libs.paper_explanations.tests.fixtures.claim_evidence import evidence


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
    source = evidence()
    claim = ClaimExtractionRules.request(
        source.snapshot.snapshot_id,
        source,
    )
    request = StructuredGenerationRequest(
        task_kind="claim_extraction",
        input_fingerprint=claim.input_fingerprint,
        system_prompt=claim.system_prompt,
        payload_json=claim.payload_json,
        schema_name="paper_claims",
        response_schema_json=claim.response_schema_json,
        model_name=claim.model_name,
    )

    result = adapter(request)
    payload = json.loads(result.content_json)
    parsed = ClaimExtractionRules.parse(
        claim,
        ClaimCandidate(
            "run:live-claim",
            MODEL_NAME,
            result.receipt.finish_reason or "",
            result.content_json,
        ),
    )

    assert payload["schema_version"] == claim.schema_version
    assert payload["snapshot_id"] == claim.snapshot_id
    assert payload["input_fingerprint"] == claim.input_fingerprint
    assert parsed.request == claim
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
