import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_registry_adapter import SqliteObjectRegistryAdapter
from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.object_registry_port import ObjectRegistryPort


class SqliteObjectUnitOfWorkAdapter:
    def __init__(self, factory: SqliteConnectionFactory) -> None:
        self._factory = factory

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[ObjectRegistryPort]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._factory.connect()
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with connection:
                yield SqliteObjectRegistryAdapter(connection)
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = "database_busy" if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} else "database_error"
            raise StorageError(code, str(exc)) from exc
        finally:
            if connection is not None:
                connection.close()
