from dataclasses import dataclass

from libs.kernel.exceptions.storage_error import StorageError


OBJECT_KINDS = frozenset({"raw", "fulltext", "extracted", "evidence", "model_output", "embedding", "digest"})


@dataclass(frozen=True)
class ObjectRef:
    object_id: str
    content_sha256: str
    relative_path: str
    kind: str
    media_type: str
    byte_size: int
    created_at: str
    retention_policy: str
    state: str = "available"

    def __post_init__(self) -> None:
        fields = (self.object_id, self.content_sha256, self.relative_path, self.kind,
                  self.media_type, self.created_at, self.retention_policy, self.state)
        if any(not isinstance(value, str) for value in fields):
            raise StorageError("invalid_object", "text fields")
        digest = self.content_sha256
        if self.kind not in OBJECT_KINDS or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise StorageError("invalid_object", "kind or SHA256")
        expected = f"objects/{self.kind}/{digest[:2]}/{digest}"
        if self.object_id != f"{self.kind}:{digest}" or self.relative_path != expected:
            raise StorageError("invalid_object", "identity or relative path")
        if type(self.byte_size) is not int or self.byte_size < 0:
            raise StorageError("invalid_object", "byte size")
        if self.state not in {"available", "missing", "quarantined"}:
            raise StorageError("invalid_object", "state")
        if (not self.media_type.strip() or not self.retention_policy.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in self.media_type + self.retention_policy)):
            raise StorageError("invalid_object", "metadata")
