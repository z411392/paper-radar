from typing import Protocol

from libs.kernel.dtos.object_ref import ObjectRef


class PublishObjectPort(Protocol):
    def __call__(self, content: bytes, kind: str, media_type: str, retention_policy: str) -> ObjectRef: ...
