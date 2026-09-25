from typing import Protocol

from libs.retrieval.dtos.search_document import (
    SearchProjectionDocument,
    SearchProjectionRow,
)


class SearchDocumentReadPort(Protocol):
    def read(self, document: SearchProjectionDocument) -> SearchProjectionRow: ...
