import http.client
import json
import os
import ssl
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

    body = adapter._wire(request)
    wire = json.loads(body)
    provider_schema = wire["response_format"]["json_schema"]["schema"]

    def drop_nested_additional_properties(
        schema: object,
        *,
        depth: int = 0,
    ) -> None:
        if not isinstance(schema, dict):
            return
        if depth > 0:
            schema.pop("additionalProperties", None)
        for key in ("properties", "$defs"):
            mapping = schema.get(key)
            if isinstance(mapping, dict):
                for child in mapping.values():
                    drop_nested_additional_properties(
                        child,
                        depth=depth + 1,
                    )
        items = schema.get("items")
        if isinstance(items, dict):
            drop_nested_additional_properties(
                items,
                depth=depth + 1,
            )
        for key in ("prefixItems", "anyOf", "oneOf"):
            children = schema.get(key)
            if isinstance(children, list):
                for child in children:
                    drop_nested_additional_properties(
                        child,
                        depth=depth + 1,
                    )

    drop_nested_additional_properties(provider_schema)
    body = json.dumps(
        wire,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    connection = http.client.HTTPSConnection(
        "openrouter.ai",
        port=443,
        timeout=30,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "POST",
            "/api/v1/chat/completions",
            body=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-OpenRouter-Metadata": "enabled",
            },
        )
        response = connection.getresponse()
        content = response.read(65537)
    finally:
        connection.close()

    assert api_key.encode() not in content
    if response.status != 200:
        safe_body = content.decode(
            "utf-8",
            errors="replace",
        )[:4000]
        raise AssertionError(
            "OpenRouter diagnostic "
            f"status={response.status} body={safe_body!r}"
        )

    envelope = json.loads(content)
    assert envelope.get("id")
    assert envelope.get("model") == MODEL_NAME
