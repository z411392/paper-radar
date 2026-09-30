from libs.kernel.ports.publish_object_port import PublishObjectPort
from libs.kernel.ports.read_object_port import ReadObjectPort
from libs.paper_explanations.ports.generation_output_store_port import GenerationOutputStorePort


class KernelGenerationOutputAdapter(GenerationOutputStorePort):
    def __init__(self, publish: PublishObjectPort, read: ReadObjectPort) -> None:
        self._publish = publish
        self._read = read

    def publish(self, content: bytes) -> str:
        ref = self._publish(
            content,
            "model_output",
            "application/json; charset=utf-8",
            "generation-output-v1",
        )
        return ref.object_id

    def read(self, object_id: str) -> bytes:
        return self._read(object_id)
