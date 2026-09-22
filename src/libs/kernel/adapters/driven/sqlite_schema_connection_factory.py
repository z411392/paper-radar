import sqlite3
from pathlib import Path

from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError


class SqliteSchemaConnectionFactory:
    """Open a recognized workspace without installing or changing migrations.

    By default the installed schema must exactly match the supplied bundle. A
    feature may instead declare a minimum version while still supplying every
    migration version this application knows how to validate.
    """

    def __init__(
        self,
        root: Path,
        migrations: tuple[Migration, ...],
        *,
        minimum_version: int | None = None,
    ) -> None:
        if not migrations or [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
            raise StorageError("invalid_migrations", "explicit contiguous bundle required")
        if minimum_version is None:
            required = len(migrations)
        elif type(minimum_version) is not int or not 1 <= minimum_version <= len(migrations):
            raise StorageError("invalid_migrations", "minimum version must exist in the known bundle")
        else:
            required = minimum_version
        self._factory = SqliteConnectionFactory(root)
        self._expected = tuple((m.version, m.name, m.sha256) for m in migrations)
        self._required = required

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
            if len(rows) < self._required:
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
