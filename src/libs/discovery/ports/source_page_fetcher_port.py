from typing import Protocol

from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_page_request import SourcePageRequest


class SourcePageFetcherPort(Protocol):
    def fetch(self, request: SourcePageRequest) -> SourceFetchResult: ...
