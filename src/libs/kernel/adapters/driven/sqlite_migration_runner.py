import sqlite3
from datetime import datetime, timezone

from libs.kernel.dtos.migration import Migration
from libs.kernel.exceptions.storage_error import StorageError


class SqliteMigrationRunner:
    def __init__(self, migrations: tuple[Migration, ...]) -> None:
        if not migrations or [m.version for m in migrations] != list(range(1, len(migrations) + 1)):
            raise StorageError("invalid_migrations", "an explicit contiguous sequence starting at 1 is required")
        if len({m.name for m in migrations}) != len(migrations):
            raise StorageError("invalid_migrations", "duplicate names")
        self._migrations = migrations

    @staticmethod
    def _authorize(action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
        prohibited = {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_ATTACH,
                      sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA}
        return sqlite3.SQLITE_DENY if action in prohibited else sqlite3.SQLITE_OK

    @staticmethod
    def _statements(sql: str) -> tuple[str, ...]:
        statements: list[str] = []
        pending = ""
        for char in sql:
            pending += char
            if char == ";" and sqlite3.complete_statement(pending):
                statements.append(pending)
                pending = ""
        if pending.strip():
            statements.append(pending + "\n;")
        return tuple(statements)

    def apply(self, connection: sqlite3.Connection) -> int:
        if connection.in_transaction:
            raise StorageError("nested_transaction", "migration runner owns its transaction")
        try:
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
                rows = connection.execute(
                    "SELECT version,name,sha256 FROM schema_migrations ORDER BY version"
                ).fetchall() if exists else []
                if [row[0] for row in rows] != list(range(1, len(rows) + 1)):
                    raise StorageError("migration_drift", "non-contiguous installed history")
                if len(rows) > len(self._migrations):
                    raise StorageError("unsupported_schema", "installed schema is newer than the selected bundle")
                for row, migration in zip(rows, self._migrations):
                    if tuple(row) != (migration.version, migration.name, migration.sha256):
                        raise StorageError("migration_drift", migration.name)
                for migration in self._migrations[len(rows):]:
                    connection.set_authorizer(self._authorize)
                    try:
                        for statement in self._statements(migration.sql):
                            connection.execute(statement)
                    finally:
                        connection.set_authorizer(None)
                    connection.execute(
                        "INSERT INTO schema_migrations(version,name,sha256,applied_at) VALUES(?,?,?,?)",
                        (migration.version, migration.name, migration.sha256, datetime.now(timezone.utc).isoformat()),
                    )
        except sqlite3.Error as exc:
            raise StorageError("migration_failed", str(exc)) from exc
        return len(self._migrations)
