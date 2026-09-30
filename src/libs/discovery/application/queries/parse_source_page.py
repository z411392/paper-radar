from libs.discovery.dtos.parsed_arxiv_page import ParsedArxivPage
from libs.discovery.dtos.source_page_request import SourcePageRequest
from libs.discovery.ports.source_page_parser_port import SourcePageParserPort


class ParseSourcePage:
    def __init__(self, parser: SourcePageParserPort) -> None:
        self._parser = parser

    def __call__(self, request: SourcePageRequest, body: bytes, *, http_status: int) -> ParsedArxivPage:
        return self._parser(request, body, http_status=http_status)
