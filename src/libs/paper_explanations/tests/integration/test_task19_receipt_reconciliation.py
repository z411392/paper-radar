from unittest.mock import Mock

import pytest

from libs.paper_explanations.dtos.model_http_response import ModelHttpResponse
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.tests.contract.test_task19_openrouter import adapter, envelope, request


@pytest.mark.parametrize("status,code", [
    (402, "budget_blocked"), (429, "rate_limited"), (503, "provider_unavailable"),
])
def test_http_failure_keeps_reported_charge_without_retaining_error_body(status, code):
    value = envelope(error={"message": "PRIVATE-diagnostic-must-not-escape"})
    run, http = adapter(value, status=status)
    with pytest.raises(ModelGatewayError, match=code) as raised:
        run(request())
    receipt = raised.value.receipt
    assert receipt.cost_usd == "0.0005323725"
    assert receipt.input_tokens == 282 and receipt.output_tokens == 87
    assert receipt.generation_id == "gen-test-19"
    assert "PRIVATE" not in repr(receipt) + str(raised.value)
    http.post.assert_called_once()


@pytest.mark.parametrize("field,value", [
    ("prompt_tokens", True), ("completion_tokens", -1), ("prompt_tokens", "282"),
])
def test_bad_token_metadata_does_not_erase_valid_reported_charge(field, value):
    response = envelope()
    response["usage"][field] = value
    run, http = adapter(response)
    with pytest.raises(ModelGatewayError, match="invalid_model_usage") as raised:
        run(request())
    receipt = raised.value.receipt
    assert receipt.cost_usd == "0.0005323725"
    target = "input_tokens" if field == "prompt_tokens" else "output_tokens"
    assert getattr(receipt, target) is None
    http.post.assert_called_once()


@pytest.mark.parametrize("value", [True, -1, 999])
def test_inconsistent_total_tokens_is_rejected_but_charge_is_retained(value):
    response = envelope()
    response["usage"]["total_tokens"] = value
    run, _ = adapter(response)
    with pytest.raises(ModelGatewayError, match="invalid_model_usage") as raised:
        run(request())
    assert raised.value.receipt.cost_usd == "0.0005323725"


def test_invalid_optional_provider_is_omitted_but_safe_cost_is_retained():
    run, _ = adapter(envelope(provider="PRIVATE\nprovider"))
    with pytest.raises(ModelGatewayError, match="invalid_model_response") as raised:
        run(request())
    receipt = raised.value.receipt
    assert receipt.cost_usd == "0.0005323725"
    assert receipt.provider is None and "PRIVATE" not in repr(receipt)


@pytest.mark.parametrize("body", [b"<html>PRIVATE</html>", b'{"usage":{"cost":NaN}}'])
def test_unparseable_http_error_keeps_status_and_unknown_charge(body):
    run, http = adapter(status=429)
    http.post.return_value = ModelHttpResponse(429, body)
    with pytest.raises(ModelGatewayError, match="rate_limited") as raised:
        run(request())
    assert raised.value.receipt.cost_usd is None
    assert "PRIVATE" not in repr(raised.value.receipt)
    http.post.assert_called_once()


def test_http_status_is_preserved_even_when_some_usage_is_invalid():
    response = envelope()
    response["usage"]["prompt_tokens"] = False
    run, _ = adapter(response, status=503)
    with pytest.raises(ModelGatewayError, match="provider_unavailable") as raised:
        run(request())
    assert raised.value.receipt.cost_usd == "0.0005323725"
    assert raised.value.receipt.input_tokens is None


@pytest.mark.parametrize("literal", ["0e-1000", "-0e-1000"])
def test_zero_exponent_cost_has_bounded_output(literal):
    run, http = adapter()
    http.post.return_value = ModelHttpResponse(200, (
        '{"id":"gen-zero","model":"google/gemini-3.8-flash",'
        '"choices":[{"finish_reason":"stop","message":{"role":"assistant",'
        '"content":"{}"}}],"usage":{"cost":' + literal + '}}'
    ).encode())
    receipt = run(request()).receipt
    assert receipt.cost_usd == "0"


def test_missing_cost_stays_unknown_after_http_failure():
    run, _ = adapter(envelope(usage=None), status=503)
    with pytest.raises(ModelGatewayError) as raised:
        run(request())
    assert raised.value.receipt.cost_usd is None


def test_transport_error_cannot_inject_an_unrelated_receipt():
    run, http = adapter()
    forged = Mock(cost_usd="999")
    http.post.side_effect = ModelGatewayError("timeout", forged)
    with pytest.raises(ModelGatewayError, match="timeout") as raised:
        run(request())
    assert raised.value.receipt is not forged
    assert raised.value.receipt.cost_usd is None
    assert raised.value.receipt.input_fingerprint == request().input_fingerprint
