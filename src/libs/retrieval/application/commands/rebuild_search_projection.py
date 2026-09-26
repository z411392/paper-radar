from libs.retrieval.dtos.search_document import SearchProjectionRebuildResult
from libs.retrieval.exceptions.search_projection_error import SearchProjectionError
from libs.retrieval.ports.search_document_read_port import SearchDocumentReadPort
from libs.retrieval.ports.search_document_store_port import SearchDocumentStorePort
from libs.retrieval.ports.search_projection_writer_port import SearchProjectionWriterPort


class RebuildSearchProjection:
    def __init__(
        self,
        documents: SearchDocumentStorePort,
        reader: SearchDocumentReadPort,
        writer: SearchProjectionWriterPort,
    ) -> None:
        self._documents = documents
        self._reader = reader
        self._writer = writer

    def __call__(
        self,
        *,
        maximum_documents: int = 100_000,
    ) -> SearchProjectionRebuildResult:
        if (
            type(maximum_documents) is not int
            or not 1 <= maximum_documents <= 1_000_000
        ):
            raise SearchProjectionError("invalid_search_projection_limit")
        documents = self._documents.current_documents()
        if len(documents) > maximum_documents:
            raise SearchProjectionError("search_projection_limit_exceeded")

        rows = tuple(self._reader.read(document) for document in documents)
        if tuple(row.document_id for row in rows) != tuple(
            document.document_id for document in documents
        ):
            raise SearchProjectionError("search_projection_identity_mismatch")

        self._writer.replace(rows)
        return SearchProjectionRebuildResult(
            len(rows),
            tuple(row.document_id for row in rows),
        )
