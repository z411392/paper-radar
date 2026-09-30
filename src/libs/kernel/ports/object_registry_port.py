from typing import Protocol

from libs.kernel.dtos.object_ref import ObjectRef


class ObjectRegistryPort(Protocol):
    def get(self, object_id: str) -> ObjectRef | None: ...

    def add(self, ref: ObjectRef) -> None: ...

    def all(self) -> tuple[ObjectRef, ...]: ...
