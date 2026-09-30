from typing import Protocol

from libs.discovery.dtos.parsed_arxiv_page import ParsedArxivPage
from libs.discovery.dtos.source_page_request import SourcePageRequest


class SourcePageParserPort(Protocol):
    def __call__(self, request: SourcePageRequest, body: bytes, *, http_status: int) -> ParsedArxivPage: ...
