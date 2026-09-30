from contextlib import AbstractContextManager
from typing import Protocol

from libs.kernel.ports.object_registry_port import ObjectRegistryPort


class ObjectUnitOfWorkPort(Protocol):
    def transaction(self, *, write: bool = True) -> AbstractContextManager[ObjectRegistryPort]: ...
