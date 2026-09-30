from typing import Protocol

from libs.discovery.dtos.source_http_response import SourceHttpResponse
from libs.discovery.dtos.source_page_request import SourcePageRequest


class SourceHttpTransportPort(Protocol):
    def get(self, request: SourcePageRequest) -> SourceHttpResponse: ...
