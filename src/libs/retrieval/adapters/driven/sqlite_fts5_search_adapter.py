import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.retrieval.dtos.search_document import SearchProjectionRow
from libs.retrieval.exceptions.search_projection_error import SearchProjectionError
from libs.retrieval.ports.search_projection_writer_port import SearchProjectionWriterPort


class SqliteFts5SearchAdapter(SearchProjectionWriterPort):
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            if connection.in_transaction:
                raise SearchProjectionError("owned_connection_required")
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except SearchProjectionError:
            raise
        except sqlite3.IntegrityError as exc:
            raise SearchProjectionError(
                "search_projection_database_conflict"
            ) from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "search_projection_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "search_projection_database_error"
            )
            raise SearchProjectionError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _validate(rows: tuple[SearchProjectionRow, ...]) -> None:
        if not isinstance(rows, tuple):
            raise SearchProjectionError("invalid_search_projection_rows")
        seen = set()
        for row in rows:
            if (
                not isinstance(row, SearchProjectionRow)
                or not isinstance(row.document_id, str)
                or not row.document_id
                or row.document_id in seen
                or any(
                    not isinstance(value, str) or "\0" in value
                    for value in (row.title, row.abstract, row.explanation)
                )
            ):
                raise SearchProjectionError("invalid_search_projection_rows")
            seen.add(row.document_id)

    def replace(self, rows: tuple[SearchProjectionRow, ...]) -> None:
        self._validate(rows)
        values = tuple(
            (
                row.document_id,
                row.title,
                row.abstract,
                row.explanation,
            )
            for row in rows
        )
        with self._transaction() as connection:
            connection.execute("DELETE FROM search_documents_fts")
            connection.execute("DELETE FROM search_documents_fts_trigram")
            if values:
                connection.executemany(
                    "INSERT INTO search_documents_fts("
                    "document_id,title,abstract,explanation"
                    ") VALUES(?,?,?,?)",
                    values,
                )
                connection.executemany(
                    "INSERT INTO search_documents_fts_trigram("
                    "document_id,title,abstract,explanation"
                    ") VALUES(?,?,?,?)",
                    values,
                )
            unicode_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT document_id FROM search_documents_fts "
                    "ORDER BY document_id"
                ).fetchall()
            )
            trigram_ids = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT document_id FROM search_documents_fts_trigram "
                    "ORDER BY document_id"
                ).fetchall()
            )
            expected = tuple(sorted(row.document_id for row in rows))
            if unicode_ids != expected or trigram_ids != expected:
                raise SearchProjectionError(
                    "search_projection_membership_mismatch"
                )
