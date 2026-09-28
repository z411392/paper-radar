import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from libs.paper_explanations.dtos.openrouter_policy import OpenRouterPolicy
from libs.paper_explanations.dtos.structured_generation_request import MODEL_NAME

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

COMMISSIONED_OPENROUTER_POLICY = OpenRouterPolicy(
    768,
    "1",
    "5",
    enabled=True,
)


def commissioned_openrouter_execution_policy_fingerprint() -> str:
    policy = COMMISSIONED_OPENROUTER_POLICY
    canonical = json.dumps(
        {
            "format_version": 1,
            "gateway": "openrouter",
            "endpoint": OPENROUTER_ENDPOINT,
            "model": MODEL_NAME,
            "max_output_tokens": policy.max_output_tokens,
            "max_prompt_price": policy.max_prompt_price,
            "max_completion_price": policy.max_completion_price,
            "max_request_bytes": policy.max_request_bytes,
            "max_response_bytes": policy.max_response_bytes,
            "timeout_seconds": policy.timeout_seconds,
            "provider": {
                "require_parameters": True,
                "allow_fallbacks": False,
                "data_collection": "deny",
                "request_price": 0,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def commissioned_openrouter_budget_period(
    now: datetime | None = None,
) -> tuple[str, str]:
    current = datetime.now(timezone.utc) if now is None else now
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("timezone_required")
    utc = current.astimezone(timezone.utc)
    return (f"{utc.year:04d}-{utc.month:02d}", "USD")



def model_budget_usd_to_micros(value: str) -> int:
    try:
        if (
            not isinstance(value, str)
            or re.fullmatch(
                r"(?:0|[0-9]{1,6})(?:\.[0-9]{1,6})?",
                value,
            )
            is None
        ):
            raise ValueError("budget")
        amount = Decimal(value)
        if (
            not amount.is_finite()
            or amount <= 0
            or amount > Decimal("1000000")
        ):
            raise ValueError("budget")
        micros = amount * Decimal(1_000_000)
        if micros != micros.to_integral_value():
            raise ValueError("budget")
        result = int(micros)
        if not 1 <= result < 2**63:
            raise ValueError("budget")
        return result
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError("invalid_model_budget_usd") from None
