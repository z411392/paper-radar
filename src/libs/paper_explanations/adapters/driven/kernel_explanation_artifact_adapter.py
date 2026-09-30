from libs.kernel.ports.publish_object_port import PublishObjectPort
from libs.paper_explanations.ports.explanation_artifact_store_port import ExplanationArtifactStorePort


class KernelExplanationArtifactAdapter(ExplanationArtifactStorePort):
    def __init__(self, publish: PublishObjectPort) -> None:
        self._publish = publish

    def publish(self, content: bytes) -> str:
        ref = self._publish(
            content,
            "model_output",
            "application/json; charset=utf-8",
            "verified-explanation-v1",
        )
        return ref.object_id
