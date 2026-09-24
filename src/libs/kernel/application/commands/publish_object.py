import hashlib
from datetime import datetime, timezone

from libs.kernel.dtos.object_ref import ObjectRef
from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.object_bytes_port import ObjectBytesPort
from libs.kernel.ports.object_unit_of_work_port import ObjectUnitOfWorkPort


class PublishObject:
    def __init__(self, files: ObjectBytesPort, uow: ObjectUnitOfWorkPort) -> None:
        self._files = files
        self._uow = uow

    @staticmethod
    def _match(existing: ObjectRef, expected: ObjectRef) -> None:
        if existing.state != "available":
            raise StorageError("object_unavailable", existing.object_id)
        actual = (
            existing.object_id, existing.content_sha256, existing.relative_path,
            existing.kind, existing.byte_size, existing.media_type, existing.retention_policy,
        )
        wanted = (
            expected.object_id, expected.content_sha256, expected.relative_path,
            expected.kind, expected.byte_size, expected.media_type, expected.retention_policy,
        )
        if actual != wanted:
            raise StorageError("metadata_conflict", expected.object_id)

    def __call__(self, content: bytes, kind: str, media_type: str, retention_policy: str) -> ObjectRef:
        if not isinstance(content, bytes):
            raise StorageError("invalid_object", "content must be bytes")
        digest = hashlib.sha256(content).hexdigest()
        expected = ObjectRef(
            f"{kind}:{digest}", digest, f"objects/{kind}/{digest[:2]}/{digest}",
            kind, media_type, len(content), datetime.now(timezone.utc).isoformat(), retention_policy,
        )
        # Ordinary replay is not repair authority for registered evidence. Check the
        # registry before any filesystem publication, including missing/quarantined rows.
        with self._uow.transaction(write=False) as registry:
            existing = registry.get(expected.object_id)
        if existing is not None:
            self._match(existing, expected)
            self._files.read(existing)
            # The file read is outside SQLite. Do not return a stale available ref if
            # the registry was quarantined or changed while that I/O was in progress.
            with self._uow.transaction(write=False) as registry:
                current = registry.get(expected.object_id)
            if current != existing:
                raise StorageError("object_changed", expected.object_id)
            return existing

        # No registered identity: a file failure cannot enter the writer transaction.
        # A SQL failure leaves a complete unregistered object that exact replay can adopt.
        ref = self._files.publish(content, kind, media_type, retention_policy)
        self._match(ref, expected)
        with self._uow.transaction() as registry:
            concurrent = registry.get(ref.object_id)
            if concurrent is not None:
                self._match(concurrent, expected)
                return concurrent
            registry.add(ref)
        return ref
