from typing import Protocol

from libs.retrieval.dtos.search_document import (
    PersistedSearchDocument,
    PreparedSearchDocument,
)


class SearchDocumentStorePort(Protocol):
    def validate(self, document: PreparedSearchDocument) -> None: ...

    def save(self, document: PreparedSearchDocument) -> PersistedSearchDocument: ...
