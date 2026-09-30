import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.dtos.object_ref import ObjectRef
from libs.kernel.dtos.storage_report import StorageProblem, StorageReport
from libs.kernel.exceptions.storage_error import StorageError


class FilesystemObjectBytesAdapter:
    def __init__(self, root: Path) -> None:
        self._paths = WorkspacePaths(root)

    def publish(self, content: bytes, kind: str, media_type: str, retention_policy: str) -> ObjectRef:
        if not isinstance(content, bytes):
            raise StorageError("invalid_object", "content must be bytes")
        digest = hashlib.sha256(content).hexdigest()
        ref = ObjectRef(
            f"{kind}:{digest}",
            digest,
            f"objects/{kind}/{digest[:2]}/{digest}",
            kind,
            media_type,
            len(content),
            datetime.now(timezone.utc).isoformat(),
            retention_policy,
        )
        try:
            if not self._paths.database().is_file():
                raise StorageError("workspace_missing")
            parent = self._paths.directory(f"objects/{kind}/{digest[:2]}")
            target = self._paths.path(ref.relative_path)
            temporary = self._paths.directory("tmp")
            fd, name = tempfile.mkstemp(prefix=".object-", dir=temporary)
            staging = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(staging, target)
                except FileExistsError:
                    pass
                self._paths.sync_directory(parent)
                self.read(ref)
            finally:
                staging.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("file_io", str(exc)) from exc
        return ref

    def read(self, ref: ObjectRef) -> bytes:
        try:
            content = self._paths.read_regular(self._paths.path(ref.relative_path), ref.byte_size + 1)
        except FileNotFoundError as exc:
            raise StorageError("missing", ref.object_id) from exc
        except OSError as exc:
            raise StorageError("file_io", str(exc)) from exc
        if len(content) != ref.byte_size or hashlib.sha256(content).hexdigest() != ref.content_sha256:
            raise StorageError("corrupt", ref.object_id)
        return content

    def inspect(self, refs: tuple[ObjectRef, ...]) -> StorageReport:
        problems: list[StorageProblem] = []
        registered = {ref.relative_path for ref in refs}
        for ref in refs:
            try:
                self.read(ref)
                if ref.state != "available":
                    problems.append(StorageProblem("object_unavailable", ref.relative_path, ref.object_id))
            except StorageError as exc:
                problems.append(StorageProblem(exc.code, ref.relative_path, ref.object_id))
        try:
            base = self._paths.path("objects")
            if not base.is_dir():
                problems.append(StorageProblem("missing_directory", "objects"))
            for directory, dirs, filenames in os.walk(base, followlinks=False, onerror=self._walk_error):
                for name in list(dirs):
                    path = Path(directory) / name
                    if path.is_symlink():
                        dirs.remove(name)
                        problems.append(
                            StorageProblem("unsafe_path", path.relative_to(self._paths.root).as_posix())
                        )
                for name in filenames:
                    path = Path(directory) / name
                    relative = path.relative_to(self._paths.root).as_posix()
                    if relative not in registered:
                        code = "unsafe_path" if path.is_symlink() else "unregistered"
                        problems.append(StorageProblem(code, relative))
            temporary = self._paths.path("tmp")
            if temporary.is_dir():
                for path in temporary.iterdir():
                    relative = path.relative_to(self._paths.root).as_posix()
                    code = "unsafe_path" if path.is_symlink() else "temporary"
                    problems.append(StorageProblem(code, relative))
        except OSError as exc:
            raise StorageError("file_io", str(exc)) from exc
        return StorageReport(len(refs), tuple(sorted(problems, key=lambda p: (p.relative_path, p.code))))

    @staticmethod
    def _walk_error(error: OSError) -> None:
        raise error
