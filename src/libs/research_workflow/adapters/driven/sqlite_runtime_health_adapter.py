import platform
import sqlite3
import sys
from collections.abc import Callable

from libs.research_workflow.dtos.operational_health import RuntimeHealthEvidence
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
)


class SqliteRuntimeHealthAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def __call__(self) -> RuntimeHealthEvidence:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                "SELECT sqlite_version(),sqlite_source_id()"
            ).fetchone()
        except sqlite3.Error as exc:
            raise OperationalHealthError("runtime_health_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        if (
            row is None
            or not isinstance(row[0], str)
            or not row[0]
            or not isinstance(row[1], str)
            or not row[1]
        ):
            raise OperationalHealthError("runtime_health_corrupt")
        return RuntimeHealthEvidence(
            python_implementation=sys.implementation.name,
            python_version=platform.python_version(),
            sqlite_version=row[0],
            sqlite_source_id=row[1],
        )
