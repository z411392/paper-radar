import hashlib
import json
import re
from dataclasses import asdict
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from libs.paper_explanations.dtos.generation_budget_policy import GenerationBudgetPolicy
from libs.paper_explanations.dtos.generation_identity import GenerationIdentity
from libs.paper_explanations.dtos.structured_generation_request import StructuredGenerationRequest
from libs.paper_explanations.dtos.structured_generation_result import (
    GenerationReceipt,
    StructuredGenerationResult,
)
from libs.paper_explanations.exceptions.generation_ledger_error import GenerationLedgerError


class GenerationExecutionRules:
    NO_CHARGE_CODES = frozenset(
        {
            "model_disabled",
            "model_not_allowed",
            "invalid_model_policy",
            "invalid_generation_request",
            "request_too_large",
        }
    )

    @staticmethod
    def _canonical(value: object) -> str:
        try:
            text = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            text.encode("utf-8")
            return text
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise GenerationLedgerError("invalid_generation_identity") from exc

    @staticmethod
    def _hex(value: object, code: str) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise GenerationLedgerError(code)
        return value

    @staticmethod
    def _text(value: object, pattern: str, code: str) -> str:
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise GenerationLedgerError(code)
        return value

    @classmethod
    def validate_identity(cls, identity: GenerationIdentity) -> None:
        if not isinstance(identity, GenerationIdentity):
            raise GenerationLedgerError("invalid_generation_identity")
        cls._hex(identity.generation_fingerprint, "invalid_generation_identity")
        cls._hex(identity.request_input_fingerprint, "invalid_generation_identity")
        cls._hex(identity.prompt_digest, "invalid_generation_identity")
        cls._hex(identity.execution_policy_fingerprint, "invalid_generation_identity")
        cls._text(identity.task_kind, r"[a-z][a-z0-9_]{0,63}", "invalid_generation_identity")
        cls._text(identity.gateway, r"[a-z][a-z0-9_-]{0,31}", "invalid_generation_identity")
        cls._text(
            identity.requested_model,
            r"[a-z0-9-]+/[a-z0-9_.:-]{1,128}",
            "invalid_generation_identity",
        )
        cls._text(
            identity.period_key,
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}",
            "invalid_generation_budget",
        )
        cls._text(identity.currency, r"[A-Z]{3}", "invalid_generation_budget")
        if identity.endpoint != "https://openrouter.ai/api/v1/chat/completions":
            raise GenerationLedgerError("invalid_generation_identity")
        if (
            type(identity.period_limit_micros) is not int
            or type(identity.reservation_micros) is not int
            or identity.period_limit_micros < 0
            or identity.reservation_micros < 0
            or identity.reservation_micros > identity.period_limit_micros
            or identity.period_limit_micros >= 2**63
        ):
            raise GenerationLedgerError("invalid_generation_budget")

    @classmethod
    def identity(
        cls,
        request: StructuredGenerationRequest,
        policy: GenerationBudgetPolicy,
    ) -> GenerationIdentity:
        if not isinstance(request, StructuredGenerationRequest) or not isinstance(
            policy, GenerationBudgetPolicy
        ):
            raise GenerationLedgerError("invalid_generation_identity")
        cls._hex(request.input_fingerprint, "invalid_generation_identity")
        cls._text(request.task_kind, r"[a-z][a-z0-9_]{0,63}", "invalid_generation_identity")
        cls._text(
            request.model_name,
            r"[a-z0-9-]+/[a-z0-9_.:-]{1,128}",
            "invalid_generation_identity",
        )
        cls._hex(policy.execution_policy_fingerprint, "invalid_generation_budget")
        cls._text(
            policy.period_key,
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}",
            "invalid_generation_budget",
        )
        cls._text(policy.currency, r"[A-Z]{3}", "invalid_generation_budget")
        cls._text(policy.gateway, r"[a-z][a-z0-9_-]{0,31}", "invalid_generation_budget")
        if policy.endpoint != "https://openrouter.ai/api/v1/chat/completions":
            raise GenerationLedgerError("invalid_generation_budget")
        if (
            type(policy.period_limit_micros) is not int
            or type(policy.reservation_micros) is not int
            or policy.period_limit_micros < 0
            or policy.reservation_micros < 0
            or policy.reservation_micros > policy.period_limit_micros
            or policy.period_limit_micros >= 2**63
        ):
            raise GenerationLedgerError("invalid_generation_budget")

        prompt_digest = hashlib.sha256(
            cls._canonical(
                {
                    "system_prompt": request.system_prompt,
                    "schema_name": request.schema_name,
                    "response_schema_json": request.response_schema_json,
                }
            ).encode("utf-8")
        ).hexdigest()
        try:
            payload_bytes = request.payload_json.encode("utf-8")
        except (AttributeError, UnicodeEncodeError) as exc:
            raise GenerationLedgerError("invalid_generation_identity") from exc
        payload_digest = hashlib.sha256(payload_bytes).hexdigest()
        generation_fingerprint = hashlib.sha256(
            cls._canonical(
                {
                    "format_version": 1,
                    "gateway": policy.gateway,
                    "endpoint": policy.endpoint,
                    "requested_model": request.model_name,
                    "task_kind": request.task_kind,
                    "request_input_fingerprint": request.input_fingerprint,
                    "prompt_digest": prompt_digest,
                    "payload_digest": payload_digest,
                    "execution_policy_fingerprint": policy.execution_policy_fingerprint,
                }
            ).encode("utf-8")
        ).hexdigest()
        identity = GenerationIdentity(
            generation_fingerprint,
            request.input_fingerprint,
            request.task_kind,
            policy.gateway,
            policy.endpoint,
            request.model_name,
            prompt_digest,
            policy.execution_policy_fingerprint,
            policy.period_key,
            policy.currency,
            policy.period_limit_micros,
            policy.reservation_micros,
        )
        cls.validate_identity(identity)
        return identity

    @staticmethod
    def _receipt_dict(receipt: GenerationReceipt) -> dict[str, object]:
        if not isinstance(receipt, GenerationReceipt):
            raise GenerationLedgerError("invalid_generation_receipt")
        patterns = (
            (receipt.input_fingerprint, r"[0-9a-f]{64}"),
            (receipt.request_sha256, r"[0-9a-f]{64}"),
            (receipt.requested_model, r"[a-z0-9-]+/[a-z0-9_.:-]{1,128}"),
        )
        if any(
            not isinstance(value, str) or re.fullmatch(pattern, value) is None
            for value, pattern in patterns
        ):
            raise GenerationLedgerError("invalid_generation_receipt")
        for value, pattern in (
            (receipt.generation_id, r"[A-Za-z0-9_-]{1,128}"),
            (receipt.returned_model, r"[a-z0-9-]+/[a-z0-9_.:-]{1,128}"),
            (receipt.provider, r"[A-Za-z0-9][A-Za-z0-9 ./_-]{0,127}"),
            (receipt.finish_reason, r"[a-z_]{1,32}"),
        ):
            if value is not None and (
                not isinstance(value, str) or re.fullmatch(pattern, value) is None
            ):
                raise GenerationLedgerError("invalid_generation_receipt")
        for value in (receipt.input_tokens, receipt.output_tokens):
            if value is not None and (type(value) is not int or not 0 <= value < 2**63):
                raise GenerationLedgerError("invalid_generation_receipt")
        if receipt.cost_usd is not None:
            GenerationExecutionRules.cost_micros(receipt.cost_usd)
        return asdict(receipt)

    @classmethod
    def serialize_success(cls, result: StructuredGenerationResult) -> bytes:
        if not isinstance(result, StructuredGenerationResult) or not isinstance(
            result.content_json, str
        ):
            raise GenerationLedgerError("invalid_generation_result")
        try:
            seen_duplicate = False

            def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                nonlocal seen_duplicate
                output: dict[str, object] = {}
                for key, value in pairs:
                    if key in output:
                        seen_duplicate = True
                    output[key] = value
                return output

            parsed = json.loads(
                result.content_json,
                object_pairs_hook=unique,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
            if seen_duplicate or not isinstance(parsed, dict):
                raise ValueError("object required")
            result.content_json.encode("utf-8")
        except (
            json.JSONDecodeError,
            ValueError,
            TypeError,
            UnicodeEncodeError,
            RecursionError,
        ) as exc:
            raise GenerationLedgerError("invalid_generation_result") from exc
        if (
            result.receipt.generation_id is None
            or result.receipt.returned_model != result.receipt.requested_model
            or result.receipt.finish_reason != "stop"
        ):
            raise GenerationLedgerError("invalid_generation_result")
        payload = {
            "format_version": 1,
            "kind": "success",
            "content_json": result.content_json,
            "receipt": cls._receipt_dict(result.receipt),
        }
        return cls._canonical(payload).encode("utf-8")

    @classmethod
    def serialize_receipt(cls, receipt: GenerationReceipt) -> bytes:
        return cls._canonical(
            {"format_version": 1, "kind": "receipt", "receipt": cls._receipt_dict(receipt)}
        ).encode("utf-8")

    @classmethod
    def deserialize_success(
        cls,
        content: bytes,
        identity: GenerationIdentity,
    ) -> StructuredGenerationResult:
        try:
            if not isinstance(content, bytes) or not 1 <= len(content) <= 1048576:
                raise ValueError("size")
            text = content.decode("utf-8")
            data = json.loads(text)
            if cls._canonical(data) != text:
                raise ValueError("noncanonical")
            if not isinstance(data, dict) or set(data) != {
                "format_version",
                "kind",
                "content_json",
                "receipt",
            }:
                raise ValueError("shape")
            if data["format_version"] != 1 or data["kind"] != "success":
                raise ValueError("kind")
            raw = data["receipt"]
            if not isinstance(raw, dict) or set(raw) != set(GenerationReceipt.__dataclass_fields__):
                raise ValueError("receipt")
            receipt = GenerationReceipt(**raw)
            cls._receipt_dict(receipt)
            if receipt.input_fingerprint != identity.request_input_fingerprint:
                raise ValueError("input")
            if (
                receipt.requested_model != identity.requested_model
                or receipt.returned_model != identity.requested_model
            ):
                raise ValueError("model")
            result = StructuredGenerationResult(data["content_json"], receipt)
            cls.serialize_success(result)
            return result
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            RecursionError,
        ) as exc:
            raise GenerationLedgerError("invalid_cached_generation") from exc

    @staticmethod
    def cost_micros(cost_usd: str) -> int:
        try:
            if (
                not isinstance(cost_usd, str)
                or re.fullmatch(r"(?:0|[0-9]{1,6})(?:\.[0-9]{1,18})?", cost_usd) is None
            ):
                raise ValueError("cost")
            value = Decimal(cost_usd)
            if not value.is_finite() or value < 0 or value > Decimal("1000000"):
                raise ValueError("cost")
            micros = int(
                (value * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING)
            )
            if not 0 <= micros < 2**63:
                raise ValueError("cost")
            return micros
        except (InvalidOperation, ValueError, OverflowError):
            raise GenerationLedgerError("invalid_generation_receipt") from None

    @classmethod
    def is_known_no_charge(cls, code: str) -> bool:
        return code in cls.NO_CHARGE_CODES
