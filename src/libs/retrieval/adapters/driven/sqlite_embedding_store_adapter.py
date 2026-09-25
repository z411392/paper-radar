import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from libs.retrieval.domain.services.embedding_batch_rules import EmbeddingBatchRules
from libs.retrieval.dtos.embedding_batch import (
    PersistedEmbedding,
    PersistedEmbeddingBatch,
    PreparedEmbeddingBatch,
)
from libs.retrieval.exceptions.embedding_batch_error import EmbeddingBatchError


class SqliteEmbeddingStoreAdapter:
    _DOCUMENT_CHUNK = 500

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise EmbeddingBatchError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise EmbeddingBatchError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except EmbeddingBatchError:
            raise
        except sqlite3.IntegrityError as exc:
            raise EmbeddingBatchError("embedding_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "embedding_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "embedding_database_error"
            )
            raise EmbeddingBatchError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _require_space(
        connection: sqlite3.Connection,
        batch: PreparedEmbeddingBatch,
    ) -> None:
        row = connection.execute(
            "SELECT id,configuration_fingerprint,dimension,dtype "
            "FROM embedding_spaces WHERE id=?",
            (batch.space_id,),
        ).fetchone()
        if row is None:
            raise EmbeddingBatchError("embedding_space_missing")
        if tuple(row) != (
            batch.space_id,
            batch.space_configuration_fingerprint,
            batch.dimension,
            "float32",
        ):
            raise EmbeddingBatchError("embedding_space_conflict")

    @classmethod
    def _require_documents(
        cls,
        connection: sqlite3.Connection,
        batch: PreparedEmbeddingBatch,
    ) -> None:
        expected = {row.document_id for row in batch.rows}
        found: set[str] = set()
        document_ids = tuple(sorted(expected))
        for start in range(0, len(document_ids), cls._DOCUMENT_CHUNK):
            chunk = document_ids[start : start + cls._DOCUMENT_CHUNK]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                "SELECT id FROM search_documents WHERE id IN ("
                + placeholders
                + ")",
                chunk,
            ).fetchall()
            found.update(row["id"] for row in rows)
        if found != expected:
            raise EmbeddingBatchError("embedding_document_missing")

    @staticmethod
    def _result(row: sqlite3.Row, *, replayed: bool) -> PersistedEmbedding:
        try:
            created_at = EmbeddingBatchRules.instant(
                datetime.fromisoformat(row["created_at"])
            )
        except (
            ValueError,
            TypeError,
            OverflowError,
            EmbeddingBatchError,
        ) as exc:
            raise EmbeddingBatchError("embedding_state_corrupt") from exc
        if (
            type(row["id"]) is not int
            or row["id"] < 1
            or type(row["row_offset"]) is not int
            or row["row_offset"] < 0
        ):
            raise EmbeddingBatchError("embedding_state_corrupt")
        return PersistedEmbedding(
            row["id"],
            row["document_id"],
            row["space_id"],
            row["object_id"],
            row["row_offset"],
            row["input_fingerprint"],
            created_at,
            replayed,
        )

    @classmethod
    def _existing(
        cls,
        connection: sqlite3.Connection,
        batch: PreparedEmbeddingBatch,
    ) -> tuple[PersistedEmbedding, ...] | None:
        results = []
        missing = 0
        for expected in batch.rows:
            rows = connection.execute(
                "SELECT * FROM embeddings WHERE document_id=? AND space_id=?",
                (expected.document_id, batch.space_id),
            ).fetchall()
            if len(rows) > 1:
                raise EmbeddingBatchError("embedding_state_corrupt")
            if not rows:
                missing += 1
                continue
            row = rows[0]
            actual = (
                row["document_id"],
                row["space_id"],
                row["object_id"],
                row["row_offset"],
                row["input_fingerprint"],
            )
            wanted = (
                expected.document_id,
                batch.space_id,
                batch.object_id,
                expected.row_offset,
                expected.input_fingerprint,
            )
            if actual != wanted:
                raise EmbeddingBatchError("embedding_row_conflict")
            results.append(cls._result(row, replayed=True))
        if missing == len(batch.rows):
            return None
        if missing != 0 or len(results) != len(batch.rows):
            raise EmbeddingBatchError("embedding_row_conflict")
        return tuple(results)

    @staticmethod
    def _require_object(
        connection: sqlite3.Connection,
        batch: PreparedEmbeddingBatch,
    ) -> None:
        row = connection.execute(
            "SELECT content_sha256,relative_path,kind,media_type,byte_size,"
            "state,retention_policy FROM object_registry WHERE object_id=?",
            (batch.object_id,),
        ).fetchone()
        if row is None:
            raise EmbeddingBatchError("embedding_object_missing")
        digest = batch.object_id.removeprefix("embedding:")
        expected = (
            digest,
            f"objects/embedding/{digest[:2]}/{digest}",
            "embedding",
            "application/x-npy",
            len(batch.content_bytes),
            "available",
            "embedding-vector-batch-v1",
        )
        if tuple(row) != expected:
            raise EmbeddingBatchError("embedding_object_mismatch")

    def validate(self, batch: PreparedEmbeddingBatch) -> None:
        EmbeddingBatchRules.validate_prepared(batch)
        with self._transaction(write=False) as connection:
            self._require_space(connection, batch)
            self._require_documents(connection, batch)
            self._existing(connection, batch)

    def save(
        self,
        batch: PreparedEmbeddingBatch,
        *,
        created_at: datetime,
    ) -> PersistedEmbeddingBatch:
        EmbeddingBatchRules.validate_prepared(batch)
        created = EmbeddingBatchRules.instant(created_at)
        with self._transaction(write=True) as connection:
            self._require_space(connection, batch)
            self._require_documents(connection, batch)
            existing = self._existing(connection, batch)
            self._require_object(connection, batch)
            if existing is not None:
                return PersistedEmbeddingBatch(batch.object_id, existing)

            inserted = []
            for row in batch.rows:
                cursor = connection.execute(
                    "INSERT INTO embeddings("
                    "document_id,space_id,object_id,row_offset,"
                    "input_fingerprint,created_at"
                    ") VALUES(?,?,?,?,?,?)",
                    (
                        row.document_id,
                        batch.space_id,
                        batch.object_id,
                        row.row_offset,
                        row.input_fingerprint,
                        created.isoformat(),
                    ),
                )
                embedding_id = cursor.lastrowid
                if type(embedding_id) is not int or embedding_id < 1:
                    raise EmbeddingBatchError("embedding_state_corrupt")
                stored = connection.execute(
                    "SELECT * FROM embeddings WHERE id=?",
                    (embedding_id,),
                ).fetchone()
                if stored is None:
                    raise EmbeddingBatchError("embedding_state_corrupt")
                actual = (
                    stored["document_id"],
                    stored["space_id"],
                    stored["object_id"],
                    stored["row_offset"],
                    stored["input_fingerprint"],
                    stored["created_at"],
                )
                expected = (
                    row.document_id,
                    batch.space_id,
                    batch.object_id,
                    row.row_offset,
                    row.input_fingerprint,
                    created.isoformat(),
                )
                if actual != expected:
                    raise EmbeddingBatchError("embedding_state_corrupt")
                inserted.append(self._result(stored, replayed=False))

            verified = self._existing(connection, batch)
            if verified is None:
                raise EmbeddingBatchError("embedding_state_corrupt")
            if [item.embedding_id for item in verified] != [
                item.embedding_id for item in inserted
            ]:
                raise EmbeddingBatchError("embedding_state_corrupt")
            return PersistedEmbeddingBatch(batch.object_id, tuple(inserted))
