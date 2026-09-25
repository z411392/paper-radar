import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.retrieval.domain.services.search_document_rules import SearchDocumentRules
from libs.retrieval.dtos.search_document import (
    PersistedSearchDocument,
    PreparedSearchDocument,
    SearchProjectionDocument,
)
from libs.retrieval.exceptions.search_document_error import SearchDocumentError


class SqliteSearchDocumentStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise SearchDocumentError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise SearchDocumentError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except SearchDocumentError:
            raise
        except sqlite3.IntegrityError as exc:
            raise SearchDocumentError("search_document_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "search_document_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "search_document_database_error"
            )
            raise SearchDocumentError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _require_revision(
        connection: sqlite3.Connection,
        document: PreparedSearchDocument,
    ) -> None:
        row = connection.execute(
            "SELECT 1 FROM paper_revisions WHERE id=? AND work_id=?",
            (document.revision_id, document.work_id),
        ).fetchone()
        if row is None:
            raise SearchDocumentError("search_document_revision_missing")

    @staticmethod
    def _existing(
        connection: sqlite3.Connection,
        document: PreparedSearchDocument,
    ) -> sqlite3.Row | None:
        rows = connection.execute(
            "SELECT * FROM search_documents "
            "WHERE id=? OR (work_id=? AND projection_kind=? AND input_fingerprint=?)",
            (
                document.document_id,
                document.work_id,
                document.projection_kind,
                document.input_fingerprint,
            ),
        ).fetchall()
        if len(rows) > 1:
            raise SearchDocumentError("search_document_state_corrupt")
        if not rows:
            return None
        row = rows[0]
        expected = (
            document.document_id,
            document.work_id,
            document.revision_id,
            document.projection_kind,
            document.text_object_id,
            document.input_fingerprint,
        )
        actual = (
            row["id"],
            row["work_id"],
            row["revision_id"],
            row["projection_kind"],
            row["text_object_id"],
            row["input_fingerprint"],
        )
        if actual != expected:
            raise SearchDocumentError("search_document_conflict")
        if (
            row["is_current"] not in {0, 1}
            or type(row["sequence_no"]) is not int
            or row["sequence_no"] < 1
        ):
            raise SearchDocumentError("search_document_state_corrupt")
        return row

    @staticmethod
    def _require_object(
        connection: sqlite3.Connection,
        document: PreparedSearchDocument,
    ) -> None:
        row = connection.execute(
            "SELECT * FROM object_registry WHERE object_id=?",
            (document.text_object_id,),
        ).fetchone()
        digest = document.input_fingerprint
        expected = (
            digest,
            f"objects/extracted/{digest[:2]}/{digest}",
            "extracted",
            "application/json; charset=utf-8",
            len(document.content_bytes),
            "available",
            "search-document-v1",
        )
        if row is None:
            raise SearchDocumentError("search_document_object_missing")
        actual = (
            row["content_sha256"],
            row["relative_path"],
            row["kind"],
            row["media_type"],
            row["byte_size"],
            row["state"],
            row["retention_policy"],
        )
        if actual != expected:
            raise SearchDocumentError("search_document_object_mismatch")

    @staticmethod
    def _result(
        row: sqlite3.Row,
        *,
        replayed: bool,
    ) -> PersistedSearchDocument:
        return PersistedSearchDocument(
            row["id"],
            row["work_id"],
            row["revision_id"],
            row["projection_kind"],
            row["text_object_id"],
            row["input_fingerprint"],
            bool(row["is_current"]),
            row["sequence_no"],
            replayed,
        )

    def validate(self, document: PreparedSearchDocument) -> None:
        SearchDocumentRules.validate_prepared(document)
        with self._transaction(write=False) as connection:
            self._require_revision(connection, document)
            self._existing(connection, document)

    def save(
        self,
        document: PreparedSearchDocument,
    ) -> PersistedSearchDocument:
        SearchDocumentRules.validate_prepared(document)
        with self._transaction(write=True) as connection:
            self._require_revision(connection, document)
            existing = self._existing(connection, document)
            self._require_object(connection, document)
            if existing is not None:
                return self._result(existing, replayed=True)

            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence_no),0) FROM search_documents"
            ).fetchone()[0]
            if type(sequence) is not int or not 0 <= sequence < 2**63 - 1:
                raise SearchDocumentError("search_document_state_corrupt")
            sequence += 1

            connection.execute(
                "UPDATE search_documents SET is_current=0 "
                "WHERE work_id=? AND projection_kind=? AND is_current=1",
                (document.work_id, document.projection_kind),
            )
            inserted = connection.execute(
                "INSERT INTO search_documents("
                "id,work_id,revision_id,projection_kind,text_object_id,"
                "input_fingerprint,is_current,sequence_no"
                ") VALUES(?,?,?,?,?,?,1,?)",
                (
                    document.document_id,
                    document.work_id,
                    document.revision_id,
                    document.projection_kind,
                    document.text_object_id,
                    document.input_fingerprint,
                    sequence,
                ),
            ).rowcount
            if inserted != 1:
                raise SearchDocumentError("search_document_database_conflict")
            row = connection.execute(
                "SELECT * FROM search_documents WHERE id=?",
                (document.document_id,),
            ).fetchone()
            if row is None:
                raise SearchDocumentError("search_document_state_corrupt")
            expected = (
                document.document_id,
                document.work_id,
                document.revision_id,
                document.projection_kind,
                document.text_object_id,
                document.input_fingerprint,
                1,
                sequence,
            )
            actual = (
                row["id"],
                row["work_id"],
                row["revision_id"],
                row["projection_kind"],
                row["text_object_id"],
                row["input_fingerprint"],
                row["is_current"],
                row["sequence_no"],
            )
            if actual != expected:
                raise SearchDocumentError("search_document_state_corrupt")
            return self._result(row, replayed=False)


    def current_documents(self) -> tuple[SearchProjectionDocument, ...]:
        with self._transaction(write=False) as connection:
            rows = connection.execute(
                "SELECT d.*,o.content_sha256,o.kind,o.media_type,o.byte_size,"
                "o.state,o.retention_policy "
                "FROM search_documents d "
                "JOIN object_registry o ON o.object_id=d.text_object_id "
                "WHERE d.is_current=1 "
                "ORDER BY d.sequence_no,d.id"
            ).fetchall()
            documents = []
            seen = set()
            for row in rows:
                if (
                    row["id"] in seen
                    or not isinstance(row["id"], str)
                    or not isinstance(row["work_id"], str)
                    or not isinstance(row["revision_id"], str)
                    or not isinstance(row["projection_kind"], str)
                    or not isinstance(row["text_object_id"], str)
                    or not isinstance(row["input_fingerprint"], str)
                    or type(row["sequence_no"]) is not int
                    or row["sequence_no"] < 1
                    or row["text_object_id"]
                    != "extracted:" + row["input_fingerprint"]
                    or row["content_sha256"] != row["input_fingerprint"]
                    or row["kind"] != "extracted"
                    or row["media_type"] != "application/json; charset=utf-8"
                    or type(row["byte_size"]) is not int
                    or not 1 <= row["byte_size"] <= SearchDocumentRules.MAX_CONTENT_BYTES
                    or row["state"] != "available"
                    or row["retention_policy"] != "search-document-v1"
                ):
                    raise SearchDocumentError("search_document_state_corrupt")
                seen.add(row["id"])
                documents.append(
                    SearchProjectionDocument(
                        row["id"],
                        row["work_id"],
                        row["revision_id"],
                        row["projection_kind"],
                        row["text_object_id"],
                        row["input_fingerprint"],
                        row["sequence_no"],
                    )
                )
            return tuple(documents)
