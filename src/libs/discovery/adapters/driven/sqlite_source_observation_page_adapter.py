import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.discovery.dtos.source_observation_page import SourceObservationPage
from libs.discovery.exceptions.source_observation_read_error import (
    SourceObservationReadError as Error,
)


class SqliteSourceObservationPageAdapter:
    """List observations only after a discovery unit is durably closed."""

    _SOURCES = frozenset({"arxiv", "pubmed"})

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _unit_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"unit:[0-9a-f]{64}", value) is None
        ):
            raise Error("invalid_source_observation_unit")
        return value

    @staticmethod
    def _observation_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"observation:[0-9a-f]{64}", value) is None
        ):
            raise Error("invalid_source_observation_cursor")
        return value

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise Error("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise Error("foreign_keys_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except Error:
            raise
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "source_observation_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "source_observation_database_error"
            )
            raise Error(code) from exc
        finally:
            if connection is not None:
                connection.close()

    def __call__(
        self,
        unit_id: str,
        source: str,
        *,
        after_observation_id: str | None,
        limit: int,
    ) -> SourceObservationPage:
        unit_id = self._unit_id(unit_id)
        if source not in self._SOURCES:
            raise Error("unsupported_source_observation_source")
        if after_observation_id is not None:
            after_observation_id = self._observation_id(after_observation_id)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise Error("invalid_source_observation_limit")

        with self._read() as connection:
            unit = connection.execute(
                "SELECT u.state,b.source FROM harvest_units u "
                "JOIN source_bindings b ON b.id=u.binding_id WHERE u.id=?",
                (unit_id,),
            ).fetchone()
            if unit is None:
                raise Error("source_observation_unit_missing")
            if unit["source"] != source:
                raise Error("source_observation_source_mismatch")
            if unit["state"] not in {"succeeded", "verified_empty"}:
                raise Error("source_observation_unit_not_closed")
            if after_observation_id is not None:
                cursor = connection.execute(
                    "SELECT 1 FROM source_observations "
                    "WHERE id=? AND unit_id=? AND source=?",
                    (after_observation_id, unit_id, source),
                ).fetchone()
                if cursor is None:
                    raise Error("source_observation_cursor_mismatch")

            if after_observation_id is None:
                rows = connection.execute(
                    "SELECT id FROM source_observations "
                    "WHERE unit_id=? AND source=? ORDER BY id LIMIT ?",
                    (unit_id, source, limit + 1),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id FROM source_observations "
                    "WHERE unit_id=? AND source=? AND id>? ORDER BY id LIMIT ?",
                    (unit_id, source, after_observation_id, limit + 1),
                ).fetchall()
            complete = len(rows) <= limit
            values = tuple(row["id"] for row in rows[:limit])
            return SourceObservationPage(unit_id, source, values, complete)
