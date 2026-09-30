from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.ports.source_page_fetcher_port import SourcePageFetcherPort


class FetchSourcePage:
    def __init__(self, fetcher: SourcePageFetcherPort) -> None:
        self._fetcher = fetcher

    def __call__(self, request: SourcePageRequest) -> SourceFetchResult:
        return self._fetcher.fetch(request)
