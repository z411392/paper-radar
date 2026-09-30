from typing import Protocol

from libs.delivery.dtos.digest_artifact import StoredDigestPayload


class DigestArtifactReaderPort(Protocol):
    def read(self, object_id: str) -> StoredDigestPayload: ...
