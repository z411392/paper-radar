from libs.kernel.application.commands.publish_object import PublishObject
from libs.kernel.application.queries.read_object import ReadObject
from libs.scholarly_catalog.dtos.evidence_object_receipt import EvidenceObjectReceipt


class KernelEvidenceObjectAdapter:
    def __init__(self, publish: PublishObject, read: ReadObject) -> None:
        self._publish = publish
        self._read = read

    @staticmethod
    def _receipt(ref) -> EvidenceObjectReceipt:
        return EvidenceObjectReceipt(ref.object_id, ref.content_sha256)

    def publish_source(
        self,
        content: bytes,
        media_type: str,
        *,
        abstract_only: bool,
    ) -> EvidenceObjectReceipt:
        ref = self._publish(
            content,
            "evidence" if abstract_only else "fulltext",
            media_type,
            "evidence-source-v1",
        )
        return self._receipt(ref)

    def publish_text(self, content: bytes) -> EvidenceObjectReceipt:
        return self._receipt(
            self._publish(
                content,
                "extracted",
                "text/plain; charset=utf-8",
                "evidence-text-v1",
            )
        )

    def read(self, object_id: str) -> bytes:
        return self._read(object_id)
