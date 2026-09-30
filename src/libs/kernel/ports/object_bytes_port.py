from typing import Protocol

from libs.kernel.dtos.object_ref import ObjectRef
from libs.kernel.dtos.storage_report import StorageReport


class ObjectBytesPort(Protocol):
    def publish(self, content: bytes, kind: str, media_type: str, retention_policy: str) -> ObjectRef: ...

    def read(self, ref: ObjectRef) -> bytes: ...

    def inspect(self, refs: tuple[ObjectRef, ...]) -> StorageReport: ...
