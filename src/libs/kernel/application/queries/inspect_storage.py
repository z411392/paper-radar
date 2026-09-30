from libs.kernel.dtos.storage_report import StorageReport
from libs.kernel.ports.object_bytes_port import ObjectBytesPort
from libs.kernel.ports.object_unit_of_work_port import ObjectUnitOfWorkPort


class InspectStorage:
    def __init__(self, files: ObjectBytesPort, uow: ObjectUnitOfWorkPort) -> None:
        self._files = files
        self._uow = uow

    def __call__(self) -> StorageReport:
        with self._uow.transaction(write=False) as registry:
            refs = registry.all()
        # Observational diagnostics, not a GC authorization or an atomic FS/DB snapshot.
        return self._files.inspect(refs)
