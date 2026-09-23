from libs.paper_explanations.dtos.model_http_response import ModelHttpResponse
from libs.paper_explanations.dtos.openrouter_policy import OpenRouterPolicy
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError


class HttpsOpenRouterTransport:
    def __init__(self, policy: OpenRouterPolicy) -> None:
        self._policy = policy

    def post(self, body: bytes) -> ModelHttpResponse:
        raise ModelGatewayError("not_implemented")
