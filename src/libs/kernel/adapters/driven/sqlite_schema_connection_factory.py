import sqlite3
from pathlib import Path

from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError


class SqliteSchemaConnectionFactory:
    """Open a recognized workspace only when its installed schema matches the bundle.

    Validation never installs a migration. SQLite may maintain its own WAL sidecars;
    this is a schema/row no-mutation guarantee, not a byte-for-byte filesystem snapshot.
    """

    def __init__(self, root: Path, migrations: tuple[Migration, ...]) -> None:
        if not migrations or [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
            raise StorageError("invalid_migrations", "explicit contiguous bundle required")
        self._factory = SqliteConnectionFactory(root)
        self._expected = tuple((m.version, m.name, m.sha256) for m in migrations)

    def connect(self) -> sqlite3.Connection:
        try:
            connection = self._factory.connect()
        except sqlite3.Error as exc:
            raise StorageError("schema_verification_failed") from exc
        try:
            connection.execute("BEGIN")
            rows = tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT version,name,sha256 FROM schema_migrations ORDER BY version"
                ).fetchall()
            )
            if len(rows) > len(self._expected):
                raise StorageError("unsupported_schema")
            if rows != self._expected[: len(rows)]:
                raise StorageError("migration_drift")
            if len(rows) < len(self._expected):
                raise StorageError("schema_upgrade_required")
            identity = connection.execute(
                "SELECT workspace_id,epoch FROM workspace_metadata WHERE singleton=1"
            ).fetchone()
            if identity is None or not identity[0] or identity[1] < 1:
                raise StorageError("invalid_workspace")
            connection.commit()
            return connection
        except (sqlite3.Error, StorageError) as exc:
            connection.close()
            if isinstance(exc, StorageError):
                raise
            raise StorageError("schema_verification_failed") from exc
