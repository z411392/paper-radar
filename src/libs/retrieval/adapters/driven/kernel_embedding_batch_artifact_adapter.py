from libs.kernel.ports.publish_object_port import PublishObjectPort
from libs.retrieval.ports.embedding_batch_artifact_store_port import (
    EmbeddingBatchArtifactStorePort,
)


class KernelEmbeddingBatchArtifactAdapter(EmbeddingBatchArtifactStorePort):
    def __init__(self, publish: PublishObjectPort) -> None:
        self._publish = publish

    def publish(self, content: bytes) -> str:
        ref = self._publish(
            content,
            "embedding",
            "application/x-npy",
            "embedding-vector-batch-v1",
        )
        return ref.object_id
