from libs.paper_explanations.dtos.openrouter_policy import OpenRouterPolicy
from libs.paper_explanations.dtos.structured_generation_request import StructuredGenerationRequest
from libs.paper_explanations.dtos.structured_generation_result import StructuredGenerationResult
from libs.paper_explanations.exceptions.model_gateway_error import ModelGatewayError
from libs.paper_explanations.ports.model_http_transport_port import ModelHttpTransportPort


class OpenRouterStructuredAdapter:
    def __init__(self, transport: ModelHttpTransportPort, policy: OpenRouterPolicy) -> None:
        self._transport = transport
        self._policy = policy

    def __call__(self, request: StructuredGenerationRequest) -> StructuredGenerationResult:
        raise ModelGatewayError("not_implemented")
