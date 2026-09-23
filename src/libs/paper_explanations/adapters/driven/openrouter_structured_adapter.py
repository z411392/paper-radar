import hashlib
import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from libs.paper_explanations.domain.services.generation_json import GenerationJson
from libs.paper_explanations.dtos.model_http_response import ModelHttpResponse
from libs.paper_explanations.dtos.openrouter_policy import OpenRouterPolicy
from libs.paper_explanations.dtos.structured_generation_request import MODEL_NAME, StructuredGenerationRequest
from libs.paper_explanations.dtos.structured_generation_result import (
    GenerationReceipt,
    StructuredGenerationResult,
)
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.ports.model_http_transport_port import ModelHttpTransportPort

ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'


class OpenRouterStructuredAdapter:
    """One explicit, bounded completion. This is not a budget ledger or retry manager."""

    def __init__(self, transport: ModelHttpTransportPort, policy: OpenRouterPolicy) -> None:
        self.validate_policy(policy)
        self._transport = transport
        self._policy = policy

    @staticmethod
    def validate_policy(policy: OpenRouterPolicy) -> None:
        if not isinstance(policy, OpenRouterPolicy) or type(policy.enabled) is not bool:
            raise ModelGatewayError('invalid_model_policy')
        for value, minimum, maximum in (
            (policy.max_output_tokens, 1, 65536), (policy.max_request_bytes, 256, 1048576),
            (policy.max_response_bytes, 256, 1048576), (policy.timeout_seconds, 1, 120),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ModelGatewayError('invalid_model_policy')
        for price in (policy.max_prompt_price, policy.max_completion_price):
            try:
                if not isinstance(price, str) or re.fullmatch(r'[0-9]{1,4}(?:\.[0-9]{1,6})?', price) is None:
                    raise ValueError('price format')
                amount = Decimal(price)
                if not amount.is_finite() or not 0 < amount <= 1000:
                    raise ValueError('price bounds')
            except (ValueError, InvalidOperation):
                raise ModelGatewayError('invalid_model_policy') from None

    @staticmethod
    def _text(value: object, *, pattern: str, code: str) -> str:
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise ModelGatewayError(code)
        return value

    def _wire(self, request: StructuredGenerationRequest) -> bytes:
        code = 'invalid_generation_request'
        if not isinstance(request, StructuredGenerationRequest):
            raise ModelGatewayError(code)
        if request.model_name != MODEL_NAME:
            raise ModelGatewayError('model_not_allowed')
        self._text(request.input_fingerprint, pattern=r'[0-9a-f]{64}', code=code)
        self._text(request.task_kind, pattern=r'[a-z][a-z0-9_]{0,63}', code=code)
        self._text(request.schema_name, pattern=r'[A-Za-z][A-Za-z0-9_-]{0,63}', code=code)
        if not isinstance(request.system_prompt, str) or not request.system_prompt.strip():
            raise ModelGatewayError(code)
        payload = GenerationJson.object(request.payload_json, code, limit=self._policy.max_request_bytes)
        schema = GenerationJson.object(request.response_schema_json, code, limit=65536)
        if schema.get('type') != 'object' or schema.get('additionalProperties') is not False:
            raise ModelGatewayError(code)
        body = GenerationJson.canonical({
            'model': MODEL_NAME, 'stream': False, 'max_tokens': self._policy.max_output_tokens,
            'messages': [
                {'role': 'system', 'content': request.system_prompt},
                {'role': 'user', 'content': GenerationJson.canonical({
                    'input_fingerprint': request.input_fingerprint, 'data': payload,
                }, code)},
            ],
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': request.schema_name, 'strict': True, 'schema': schema,
            }},
            'provider': {
                'require_parameters': True, 'allow_fallbacks': False, 'data_collection': 'deny',
                'max_price': {
                    'prompt': float(self._policy.max_prompt_price),
                    'completion': float(self._policy.max_completion_price), 'request': 0,
                },
            },
        }, code).encode('utf-8')
        if len(body) > self._policy.max_request_bytes:
            raise ModelGatewayError('request_too_large')
        return body

    @staticmethod
    def _http_code(status: int) -> str:
        if 300 <= status <= 399:
            return 'redirect_rejected'
        return {
            400: 'invalid_request', 401: 'authentication_failed', 402: 'budget_blocked',
            403: 'permission_denied', 404: 'endpoint_unavailable', 408: 'timeout',
            413: 'request_too_large', 422: 'invalid_request', 429: 'rate_limited',
        }.get(status, 'provider_unavailable' if status >= 500 else 'http_error')

    @classmethod
    def _receipt(cls, data: dict, receipt: GenerationReceipt) -> GenerationReceipt:
        values = {}
        for name, source, pattern in (
            ('generation_id', 'id', r'[A-Za-z0-9_-]{1,128}'),
            ('returned_model', 'model', r'[a-z0-9-]+/[a-z0-9_.:-]{1,128}'),
            ('provider', 'provider', r'[A-Za-z0-9][A-Za-z0-9 ./_-]{0,127}'),
        ):
            value = data.get(source)
            if value is not None:
                values[name] = cls._text(value, pattern=pattern, code='invalid_model_response')
        usage = data.get('usage')
        if usage is not None:
            if not isinstance(usage, dict):
                raise ModelGatewayError('invalid_model_usage')
            for field, target in (('prompt_tokens', 'input_tokens'), ('completion_tokens', 'output_tokens')):
                amount = usage.get(field)
                if amount is not None and (type(amount) is not int or not 0 <= amount < 2**63):
                    raise ModelGatewayError('invalid_model_usage')
                values[target] = amount
            cost = usage.get('cost')
            if cost is not None:
                if type(cost) not in (Decimal, int) or cost < 0:
                    raise ModelGatewayError('invalid_model_usage')
                values['cost_usd'] = format(Decimal(cost), 'f')
        return replace(receipt, **values)

    def __call__(self, request: StructuredGenerationRequest) -> StructuredGenerationResult:
        if not self._policy.enabled:
            raise ModelGatewayError('model_disabled')
        body = self._wire(request)
        wire_hash = hashlib.sha256(ENDPOINT.encode() + b'\0' + body).hexdigest()
        receipt = GenerationReceipt(
            request.input_fingerprint, wire_hash, None, MODEL_NAME, None, None, None, None, None, None,
        )
        try:
            response = self._transport.post(body)
            if (
                not isinstance(response, ModelHttpResponse) or type(response.status) is not int
                or not 100 <= response.status <= 599 or not isinstance(response.body, bytes)
            ):
                raise ModelGatewayError('invalid_model_response')
            if response.status != 200:
                # Error bodies may echo credentials/prompts. Never surface or retain them.
                raise ModelGatewayError(self._http_code(response.status))
            if len(response.body) > self._policy.max_response_bytes:
                raise ModelGatewayError('response_too_large')
            data = GenerationJson.object(
                response.body, 'invalid_model_response', limit=self._policy.max_response_bytes, decimals=True,
            )
            receipt = self._receipt(data, receipt)
            if 'error' in data:
                error = data['error']
                status = error.get('code') if isinstance(error, dict) else None
                raise ModelGatewayError(self._http_code(status) if type(status) is int else 'provider_error')
            if receipt.returned_model != MODEL_NAME:
                raise ModelGatewayError('model_mismatch')
            if receipt.generation_id is None:
                raise ModelGatewayError('invalid_model_response')
            choices = data.get('choices')
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ModelGatewayError('invalid_model_response')
            choice = choices[0]
            finish = choice.get('finish_reason')
            if not isinstance(finish, str) or re.fullmatch(r'[a-z_]{1,32}', finish) is None:
                raise ModelGatewayError('invalid_model_response')
            receipt = replace(receipt, finish_reason=finish)
            message = choice.get('message')
            if not isinstance(message, dict) or message.get('role') != 'assistant':
                raise ModelGatewayError('invalid_model_response')
            if message.get('refusal') or finish == 'content_filter':
                raise ModelGatewayError('model_refused')
            if message.get('tool_calls') or message.get('function_call'):
                raise ModelGatewayError('unexpected_model_tools')
            if finish != 'stop':
                raise ModelGatewayError('incomplete_model_response')
            content = message.get('content')
            GenerationJson.object(content, 'invalid_model_content', limit=self._policy.max_response_bytes)
            return StructuredGenerationResult(content, receipt)
        except ModelGatewayError as exc:
            raise ModelGatewayError(exc.code, receipt) from None
