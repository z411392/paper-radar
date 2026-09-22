from typing import Protocol

from libs.kernel.dtos.storage_report import StorageReport


class InspectStoragePort(Protocol):
    def __call__(self) -> StorageReport: ...
