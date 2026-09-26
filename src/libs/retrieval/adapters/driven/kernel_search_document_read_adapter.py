from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.read_object_port import ReadObjectPort
from libs.retrieval.domain.services.search_document_rules import SearchDocumentRules
from libs.retrieval.dtos.search_document import (
    SearchProjectionDocument,
    SearchProjectionRow,
)
from libs.retrieval.exceptions.search_document_error import SearchDocumentError
from libs.retrieval.exceptions.search_projection_error import SearchProjectionError
from libs.retrieval.ports.search_document_read_port import SearchDocumentReadPort


class KernelSearchDocumentReadAdapter(SearchDocumentReadPort):
    def __init__(self, read_object: ReadObjectPort) -> None:
        self._read_object = read_object

    def read(self, document: SearchProjectionDocument) -> SearchProjectionRow:
        try:
            content = self._read_object(document.text_object_id)
            return SearchDocumentRules.decode_projection(content, document)
        except (StorageError, SearchDocumentError) as exc:
            raise SearchProjectionError(
                "search_projection_source_unavailable"
            ) from exc
