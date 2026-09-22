from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.object_bytes_port import ObjectBytesPort
from libs.kernel.ports.object_unit_of_work_port import ObjectUnitOfWorkPort


class ReadObject:
    def __init__(self, files: ObjectBytesPort, uow: ObjectUnitOfWorkPort) -> None:
        self._files = files
        self._uow = uow

    def __call__(self, object_id: str) -> bytes:
        with self._uow.transaction(write=False) as registry:
            ref = registry.get(object_id)
        if ref is None:
            raise StorageError("not_found", object_id)
        if ref.state != "available":
            raise StorageError("object_unavailable", object_id)
        return self._files.read(ref)
