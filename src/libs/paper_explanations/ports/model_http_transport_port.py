from typing import Protocol

from libs.paper_explanations.dtos.model_http_response import ModelHttpResponse


class ModelHttpTransportPort(Protocol):
    def post(self, body: bytes) -> ModelHttpResponse: ...
