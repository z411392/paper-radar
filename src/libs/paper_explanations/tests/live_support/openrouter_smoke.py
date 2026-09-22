"""Explicit, one-completion capability probe. Not the product LLM adapter.

Use --self-test offline. --live-confirmed consumes OPENROUTER_API_KEY from the
process environment and is only for a separately authorized bounded test.
Only fixed synthetic evidence is sent; provider bodies/secrets are never logged.
"""

import hashlib
import http.client
import json
import math
import os
import re
import ssl
import sys
import time
from typing import Any

MODEL = "google/gemini-3.8-flash"
MAX_TOKENS = 768
MAX_REQUEST_BYTES = 4000
LIVE_CONFIRMATION = "paper-radar-one-synthetic-completion-20260923"


class ProbeError(Exception):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ProbeError("duplicate_json_key")
        result[name] = value
    return result


def _constant(value: str) -> Any:
    raise ProbeError("invalid_json_number")


def decode(raw: bytes | str) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError):
        raise ProbeError("invalid_json") from None


def payload() -> dict[str, Any]:
    properties = {
        "source_id": {"type": "string"},
        "sample_size": {"type": "integer"},
        "proposed_error_m": {"type": "number"},
        "baseline_error_m": {"type": "number"},
        "external_validation": {"type": "string", "enum": ["not_reported", "reported"]},
        "summary_zh_tw": {"type": "string"},
    }
    return {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract only the supplied synthetic evidence into JSON. Write one shor"
                    "t Traditional Chinese sentence in summary_zh_tw. Do not infer missing "
                    "study details."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Synthetic abstract S1 (not a real paper): A badminton study used 180 m"
                    "atch clips. Proposed prediction error was 0.42 m versus baseline 0.58 "
                    "m. The abstract does not report whether external validation was perfor"
                    "med."
                ),
            },
        ],
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "reasoning": {"effort": "low", "exclude": True},
        "provider": {
            "allow_fallbacks": False,
            "require_parameters": True,
            "max_price": {"prompt": 1, "completion": 5, "request": 0},
        },
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "paper_radar_synthetic_probe",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            },
        },
    }


def exchange(method: str, path: str, key: str, data: bytes | None = None) -> tuple[int, Any]:
    if (method, path) not in {("GET", "/api/v1/key"), ("POST", "/api/v1/chat/completions")}:
        raise ProbeError("endpoint_not_allowed")
    if method == "POST" and (data is None or len(data) > MAX_REQUEST_BYTES):
        raise ProbeError("request_size_limit")
    connection = http.client.HTTPSConnection(
        "openrouter.ai", timeout=75, context=ssl.create_default_context()
    )
    response = None
    try:
        connection.request(
            method,
            path,
            body=data,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        status = response.status
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ProbeError("response_size_limit")
        # Never log server-supplied error messages or authentication metadata.
        if status != 200:
            return status, None
        if key.encode() in raw or b"sk-or-" in raw:
            # /key legitimately contains a masked label. It is never exposed.
            if path != "/api/v1/key":
                raise ProbeError("response_contains_secret_marker")
        return status, decode(raw)
    except ProbeError:
        raise
    except (OSError, http.client.HTTPException, UnicodeError):
        raise ProbeError("network_failure_no_retry") from None
    finally:
        try:
            if response is not None:
                response.close()
        finally:
            connection.close()


def validate_output(value: Any) -> dict[str, Any]:
    expected = {
        "source_id",
        "sample_size",
        "proposed_error_m",
        "baseline_error_m",
        "external_validation",
        "summary_zh_tw",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ProbeError("output_schema_mismatch")
    if value["source_id"] != "S1" or type(value["sample_size"]) is not int or value["sample_size"] != 180:
        raise ProbeError("source_or_sample_mismatch")
    for name, expected_number in (("proposed_error_m", 0.42), ("baseline_error_m", 0.58)):
        number = value[name]
        if type(number) not in (int, float) or not math.isfinite(number) or number != expected_number:
            raise ProbeError("numeric_alignment_mismatch")
    if value["external_validation"] != "not_reported":
        raise ProbeError("missing_evidence_not_preserved")
    summary = value["summary_zh_tw"]
    if (
        not isinstance(summary, str)
        or not 1 <= len(summary) <= 240
        or not re.search(r"[\u4e00-\u9fff]", summary)
    ):
        raise ProbeError("summary_shape_mismatch")
    if any(ord(char) < 32 for char in summary) or "sk-" in summary.lower():
        raise ProbeError("unsafe_output")
    return value


def _number(value: Any) -> int | float | None:
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return value
    return None


def probe(key: str, sender: Any = exchange) -> dict[str, Any]:
    report: dict[str, Any] = {
        "probe": "openrouter-synthetic-v1",
        "requested_model": MODEL,
        "completion_attempts": 0,
    }
    try:
        if not key or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ProbeError("missing_or_invalid_secret")
        status, metadata = sender("GET", "/api/v1/key", key)
        report["auth_http_status"] = status
        if status != 200:
            raise ProbeError("authentication_failed")
        if not isinstance(metadata, dict) or not isinstance(metadata.get("data"), dict):
            raise ProbeError("auth_response_shape")
        details = metadata["data"]
        if details.get("is_management_key") is True or details.get("is_provisioning_key") is True:
            raise ProbeError("wrong_key_kind")
        remaining = details.get("limit_remaining")
        report["key_has_limit"] = details.get("limit") is not None
        if remaining is not None and (_number(remaining) is None or remaining < 0.01):
            raise ProbeError("insufficient_key_budget_for_probe")
        report["authentication_passed"] = True
        encoded = json.dumps(payload(), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ProbeError("request_size_limit")
        report["request_sha256"] = hashlib.sha256(encoded).hexdigest()
        report["request_bytes"] = len(encoded)
        report["completion_attempts"] = 1
        started = time.monotonic()
        status, completion = sender("POST", "/api/v1/chat/completions", key, encoded)
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["completion_http_status"] = status
        if status != 200:
            raise ProbeError("completion_http_failure")
        if not isinstance(completion, dict) or "error" in completion:
            raise ProbeError("completion_error_or_shape")
        usage = completion.get("usage")
        if isinstance(usage, dict):
            report["usage"] = {
                name: _number(usage.get(name))
                for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
            }
            reasoning = usage.get("completion_tokens_details")
            if isinstance(reasoning, dict):
                report["reasoning_tokens"] = _number(reasoning.get("reasoning_tokens"))
        report["model_matches"] = completion.get("model") == MODEL
        if not report["model_matches"]:
            raise ProbeError("unexpected_returned_model")
        choices = completion.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ProbeError("choices_shape")
        choice = choices[0]
        reason = choice.get("finish_reason")
        report["finish_reason"] = (
            reason if reason in {"stop", "length", "error", "content_filter", "tool_calls"} else "unknown"
        )
        if reason != "stop":
            raise ProbeError("incomplete_completion")
        message = choice.get("message")
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("content"), str)
            or message.get("tool_calls")
        ):
            raise ProbeError("message_shape")
        content = message["content"]
        if key in content:
            raise ProbeError("response_contains_secret_marker")
        output = validate_output(decode(content))
        report.update(
            {
                "structured_output_passed": True,
                "numeric_alignment_passed": True,
                "unknown_preserved": True,
                "synthetic_output": output,
                "status": "PASS",
            }
        )
    except ProbeError as exc:
        report.update({"status": "FAIL", "failure_code": str(exc)})
    except Exception:
        report.update({"status": "FAIL", "failure_code": "internal_probe_error_no_retry"})
    return report


def self_test() -> None:
    sample = {
        "source_id": "S1",
        "sample_size": 180,
        "proposed_error_m": 0.42,
        "baseline_error_m": 0.58,
        "external_validation": "not_reported",
        "summary_zh_tw": "這是合成測試，並非真實論文。",
    }
    assert validate_output(sample) == sample
    invalid = [
        dict(sample, sample_size=True),
        dict(sample, proposed_error_m=0.58),
        dict(sample, extra="bad"),
        dict(sample, external_validation="reported"),
        dict(sample, summary_zh_tw="sk-or-secret"),
    ]
    for item in invalid:
        try:
            validate_output(item)
        except ProbeError:
            pass
        else:
            raise AssertionError("invalid output accepted")
    for raw in ('{"x":1,"x":2}', '{"x":NaN}'):
        try:
            decode(raw)
        except ProbeError:
            pass
        else:
            raise AssertionError("invalid JSON accepted")
    calls: list[str] = []

    def fake(method: str, path: str, key: str, data: bytes | None = None) -> tuple[int, Any]:
        calls.append(method)
        if method == "GET":
            return 200, {"data": {"label": key, "limit": 1, "limit_remaining": 1}}
        return 200, {
            "model": MODEL,
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(sample)}}],
            "usage": {"cost": 0.001},
        }

    result = probe("FAKE_SECRET_NEVER_PRINT", fake)
    assert result["status"] == "PASS" and calls == ["GET", "POST"]
    assert "FAKE_SECRET_NEVER_PRINT" not in json.dumps(result)
    assert probe("", fake)["completion_attempts"] == 0

    def rejected(method: str, path: str, key: str, data: bytes | None = None) -> tuple[int, Any]:
        assert method == "GET"
        return 401, None

    assert probe("FAKE_SECRET", rejected)["completion_attempts"] == 0

    def failed_post(method: str, path: str, key: str, data: bytes | None = None) -> tuple[int, Any]:
        if method == "GET":
            return 200, {"data": {}}
        return 429, None

    failure = probe("FAKE_SECRET", failed_post)
    assert failure["completion_attempts"] == 1 and failure["status"] == "FAIL"
    assert len(json.dumps(payload()).encode()) < MAX_REQUEST_BYTES
    print("PROBE_SELF_TEST=PASS; NO_NETWORK; NO_REAL_SECRET")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    elif (
        sys.argv[1:] == ["--live-confirmed"]
        and os.environ.get("PAPER_RADAR_LIVE_CONFIRMATION") == LIVE_CONFIRMATION
    ):
        result = probe(os.environ.pop("OPENROUTER_API_KEY", ""))
        print(
            "OPENROUTER_PROBE_RESULT="
            + json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        )
        raise SystemExit(0 if result["status"] == "PASS" else 1)
    else:
        print("EXPLICIT_LIVE_CONFIRMATION_REQUIRED; NO_NETWORK")
        raise SystemExit(2)
