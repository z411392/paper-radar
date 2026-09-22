import os
import stat
from pathlib import Path, PurePosixPath

from libs.kernel.exceptions.storage_error import StorageError


class WorkspacePaths:
    """Fence managed paths; the configured root and its parent are single-user trusted storage."""

    def __init__(self, root: Path) -> None:
        if ".." in root.parts or root.is_symlink():
            raise StorageError("unsafe_path", "workspace root")
        # macOS system ancestors such as /var may be symlinks; the user-selected
        # root itself and every managed descendant may not be one.
        self.root = root.resolve(strict=False)

    def path(self, relative: str) -> Path:
        parts = PurePosixPath(relative).parts
        if not parts or relative.startswith("/") or ".." in parts or "\\" in relative:
            raise StorageError("unsafe_path", relative)
        if self.root.is_symlink():
            raise StorageError("unsafe_path", "workspace root changed")
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise StorageError("unsafe_path", relative)
        return current

    def directory(self, relative: str) -> Path:
        self.path(relative)
        current = self.root
        for part in PurePosixPath(relative).parts:
            candidate = current / part
            self.path(candidate.relative_to(self.root).as_posix())
            if not candidate.exists():
                try:
                    candidate.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                self.sync_directory(current)
            if candidate.is_symlink() or not candidate.is_dir():
                raise StorageError("unsafe_path", relative)
            current = candidate
        return current

    def database(self) -> Path:
        path = self.path("state/app.sqlite3")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = self.path(f"state/app.sqlite3{suffix}")
            if sidecar.exists() and not sidecar.is_file():
                raise StorageError("unsafe_path", sidecar.name)
        if path.exists() and not path.is_file():
            raise StorageError("unsafe_path", "database is not a regular file")
        return path

    @staticmethod
    def sync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def read_regular(path: Path, limit: int) -> bytes:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise StorageError("unsafe_path", "not a regular file")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                return stream.read(limit)
        finally:
            os.close(fd)
