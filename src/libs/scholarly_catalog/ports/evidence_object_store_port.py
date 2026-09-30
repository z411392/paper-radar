from typing import Protocol

from libs.scholarly_catalog.dtos.evidence_object_receipt import EvidenceObjectReceipt


class EvidenceObjectStorePort(Protocol):
    def publish_source(
        self,
        content: bytes,
        media_type: str,
        *,
        abstract_only: bool,
    ) -> EvidenceObjectReceipt: ...

    def publish_text(self, content: bytes) -> EvidenceObjectReceipt: ...

    def read(self, object_id: str) -> bytes: ...
