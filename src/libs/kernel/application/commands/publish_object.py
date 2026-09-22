from libs.kernel.dtos.object_ref import ObjectRef
from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.object_bytes_port import ObjectBytesPort
from libs.kernel.ports.object_unit_of_work_port import ObjectUnitOfWorkPort


class PublishObject:
    def __init__(self, files: ObjectBytesPort, uow: ObjectUnitOfWorkPort) -> None:
        self._files = files
        self._uow = uow

    def __call__(self, content: bytes, kind: str, media_type: str, retention_policy: str) -> ObjectRef:
        # A file failure cannot enter the registry transaction. A SQL failure leaves
        # a complete, unregistered object that a later identical request can adopt.
        ref = self._files.publish(content, kind, media_type, retention_policy)
        with self._uow.transaction() as registry:
            existing = registry.get(ref.object_id)
            if existing is not None:
                if existing.state != "available":
                    raise StorageError("object_unavailable", existing.object_id)
                expected = (ref.relative_path, ref.byte_size, ref.media_type, ref.retention_policy)
                actual = (
                    existing.relative_path,
                    existing.byte_size,
                    existing.media_type,
                    existing.retention_policy,
                )
                if actual != expected:
                    raise StorageError("metadata_conflict", ref.object_id)
                return existing
            registry.add(ref)
        return ref
