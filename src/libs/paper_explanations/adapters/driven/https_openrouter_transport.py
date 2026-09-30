import http.client
import json
import os
import re
import ssl

from libs.paper_explanations.adapters.driven.openrouter_structured_adapter import OpenRouterStructuredAdapter
from libs.paper_explanations.dtos.model_http_response import ModelHttpResponse
from libs.paper_explanations.dtos.openrouter_policy import OpenRouterPolicy
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.ports.model_credential_port import ModelCredentialPort


class HttpsOpenRouterTransport:
    """Fixed endpoint, no redirect/retry/proxy support; credentials are read only on an authorized call."""

    def __init__(
        self,
        policy: OpenRouterPolicy,
        credential: ModelCredentialPort | None = None,
    ) -> None:
        OpenRouterStructuredAdapter.validate_policy(policy)
        self._policy = policy
        self._credential = credential

    def _key(self) -> str:
        if self._credential is None:
            key = os.environ.get("OPENROUTER_API_KEY")
        else:
            try:
                key = self._credential()
            except Exception:
                raise ModelGatewayError("credential_unavailable") from None
        if not key:
            raise ModelGatewayError("credential_missing")
        if re.fullmatch(r"[A-Za-z0-9_-]{16,512}", key) is None:
            raise ModelGatewayError("credential_invalid")
        return key

    @staticmethod
    def _credential_echo(content: bytes, key: str) -> bool:
        if key.encode() in content:
            return True
        try:
            value = json.loads(content)
        except (ValueError, UnicodeError, RecursionError):
            return False
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 64:
                raise ModelGatewayError('invalid_model_response')
            if isinstance(item, str):
                if key in item:
                    return True
                # Structured model content is itself a JSON string in the outer envelope.
                if item.lstrip().startswith(('{', '[')):
                    try:
                        pending.append((json.loads(item), depth + 1))
                    except (ValueError, RecursionError):
                        pass
            elif isinstance(item, dict):
                pending.extend((v, depth + 1) for pair in item.items() for v in pair)
            elif isinstance(item, list):
                pending.extend((v, depth + 1) for v in item)
        return False

    def post(self, body: bytes) -> ModelHttpResponse:
        if not self._policy.enabled:
            raise ModelGatewayError('model_disabled')
        if not isinstance(body, bytes) or not 1 <= len(body) <= self._policy.max_request_bytes:
            raise ModelGatewayError('request_too_large')
        key = self._key()
        connection = None
        try:
            connection = http.client.HTTPSConnection(
                'openrouter.ai', port=443, timeout=self._policy.timeout_seconds,
                context=ssl.create_default_context(),
            )
            connection.request('POST', '/api/v1/chat/completions', body=body, headers={
                'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                'Accept': 'application/json', 'Accept-Encoding': 'identity',
            })
            response = connection.getresponse()
            # Do not read/store raw provider error pages or redirect bodies.
            if response.status != 200:
                return ModelHttpResponse(response.status, b'')
            encoding = response.getheader('Content-Encoding')
            if encoding is not None and encoding.lower() != 'identity':
                raise ModelGatewayError('unsupported_response_encoding')
            declared = response.getheader('Content-Length')
            if declared is not None:
                if not isinstance(declared, str) or re.fullmatch(r'[0-9]{1,9}', declared) is None:
                    raise ModelGatewayError('invalid_model_response')
                if int(declared) > self._policy.max_response_bytes:
                    raise ModelGatewayError('response_too_large')
            content = response.read(self._policy.max_response_bytes + 1)
            if declared is not None and len(content) != int(declared):
                raise ModelGatewayError('incomplete_http_response')
            if len(content) > self._policy.max_response_bytes:
                raise ModelGatewayError('response_too_large')
            if self._credential_echo(content, key):
                raise ModelGatewayError('credential_echo_rejected')
            return ModelHttpResponse(response.status, content)
        except TimeoutError:
            raise ModelGatewayError('timeout') from None
        except (OSError, http.client.HTTPException):
            raise ModelGatewayError('transport_error') from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
