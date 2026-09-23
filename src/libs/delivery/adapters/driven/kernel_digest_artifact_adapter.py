from libs.delivery.domain.services.digest_artifact_rules import DigestArtifactRules
from libs.delivery.dtos.digest_artifact import StoredDigestPayload
from libs.delivery.dtos.digest_preview import DigestPreview
from libs.kernel.ports.publish_object_port import PublishObjectPort
from libs.kernel.ports.read_object_port import ReadObjectPort


class KernelDigestArtifactAdapter:
    def __init__(self, publish: PublishObjectPort, read: ReadObjectPort) -> None:
        self._publish = publish
        self._read = read

    def publish(self, preview: DigestPreview) -> str:
        content = DigestArtifactRules.serialize(preview)
        ref = self._publish(
            content,
            "digest",
            "application/json; charset=utf-8",
            "daily-digest-v1",
        )
        return ref.object_id

    def read(self, object_id: str) -> StoredDigestPayload:
        return DigestArtifactRules.parse(self._read(object_id))
