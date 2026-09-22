import sqlite3
from pathlib import Path

from libs.kernel.adapters.driven.workspace_paths import WorkspacePaths
from libs.kernel.exceptions.storage_error import StorageError


APPLICATION_ID = 0x50524452


class SqliteConnectionFactory:
    def __init__(self, root: Path, *, busy_timeout_ms: int = 5000) -> None:
        if type(busy_timeout_ms) is not int or not 0 <= busy_timeout_ms <= 60000:
            raise StorageError("invalid_configuration", "busy_timeout_ms")
        self._paths = WorkspacePaths(root)
        self._timeout = busy_timeout_ms

    def connect(self) -> sqlite3.Connection:
        path = self._paths.database()
        if not path.is_file():
            raise StorageError("workspace_missing")
        connection = sqlite3.connect(
            path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=self._timeout / 1000
        )
        try:
            if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                raise StorageError("foreign_database")
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self._timeout}")
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if mode != "wal":
                    raise StorageError("unsupported_journal_mode", str(mode))
            connection.execute("PRAGMA synchronous=FULL")
        except (sqlite3.Error, StorageError):
            connection.close()
            raise
        return connection
