from libs.retrieval.domain.services.search_document_rules import SearchDocumentRules
from libs.retrieval.dtos.search_document import (
    PersistedSearchDocument,
    SearchDocumentInput,
)
from libs.retrieval.exceptions.search_document_error import SearchDocumentError
from libs.retrieval.ports.search_document_artifact_store_port import (
    SearchDocumentArtifactStorePort,
)
from libs.retrieval.ports.search_document_store_port import SearchDocumentStorePort


class PrepareSearchDocument:
    def __init__(
        self,
        artifacts: SearchDocumentArtifactStorePort,
        store: SearchDocumentStorePort,
    ) -> None:
        self._artifacts = artifacts
        self._store = store

    def __call__(self, value: SearchDocumentInput) -> PersistedSearchDocument:
        prepared = SearchDocumentRules.prepare(value)
        self._store.validate(prepared)
        object_id = self._artifacts.publish(prepared.content_bytes)
        if object_id != prepared.text_object_id:
            raise SearchDocumentError("search_document_artifact_mismatch")
        return self._store.save(prepared)
