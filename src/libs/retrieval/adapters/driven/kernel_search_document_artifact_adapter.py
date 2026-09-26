from libs.kernel.ports.publish_object_port import PublishObjectPort
from libs.retrieval.ports.search_document_artifact_store_port import (
    SearchDocumentArtifactStorePort,
)


class KernelSearchDocumentArtifactAdapter(SearchDocumentArtifactStorePort):
    def __init__(self, publish: PublishObjectPort) -> None:
        self._publish = publish

    def publish(self, content: bytes) -> str:
        ref = self._publish(
            content,
            "extracted",
            "application/json; charset=utf-8",
            "search-document-v1",
        )
        return ref.object_id
