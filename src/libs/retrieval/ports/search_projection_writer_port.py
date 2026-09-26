from typing import Protocol

from libs.retrieval.dtos.search_document import SearchProjectionRow


class SearchProjectionWriterPort(Protocol):
    def replace(self, rows: tuple[SearchProjectionRow, ...]) -> None: ...
