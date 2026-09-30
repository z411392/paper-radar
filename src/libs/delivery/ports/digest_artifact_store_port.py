from typing import Protocol

from libs.delivery.dtos.digest_artifact import StoredDigestPayload
from libs.delivery.dtos.digest_preview import DigestPreview


class DigestArtifactStorePort(Protocol):
    def publish(self, preview: DigestPreview) -> str: ...

    def read(self, object_id: str) -> StoredDigestPayload: ...
