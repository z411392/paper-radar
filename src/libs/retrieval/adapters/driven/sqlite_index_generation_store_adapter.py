import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from libs.retrieval.domain.services.embedding_batch_rules import EmbeddingBatchRules
from libs.retrieval.domain.services.embedding_space_rules import EmbeddingSpaceRules
from libs.retrieval.domain.services.index_generation_rules import IndexGenerationRules
from libs.retrieval.dtos.embedding_space import PreparedEmbeddingSpace
from libs.retrieval.dtos.index_generation import (
    IndexGenerationMember,
    IndexGenerationSnapshot,
    PersistedIndexGeneration,
    PreparedIndexGeneration,
)
from libs.retrieval.exceptions.embedding_space_error import EmbeddingSpaceError
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError


class SqliteIndexGenerationStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise IndexGenerationError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise IndexGenerationError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except IndexGenerationError:
            raise
        except sqlite3.IntegrityError as exc:
            raise IndexGenerationError("index_generation_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "index_generation_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "index_generation_database_error"
            )
            raise IndexGenerationError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _space(
        connection: sqlite3.Connection,
        space_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM embedding_spaces WHERE id=?",
            (space_id,),
        ).fetchone()
        if row is None:
            raise IndexGenerationError("embedding_space_missing")
        prepared = PreparedEmbeddingSpace(
            row["id"],
            row["provider"],
            row["model_name"],
            row["model_revision"],
            row["dimension"],
            row["dtype"],
            row["normalization_version"],
            row["prefix_config_hash"],
            row["metric"],
            row["configuration_fingerprint"],
        )
        try:
            EmbeddingSpaceRules.validate_prepared(prepared)
        except EmbeddingSpaceError as exc:
            raise IndexGenerationError("embedding_space_conflict") from exc
        return row

    @staticmethod
    def _member(
        row: sqlite3.Row,
        *,
        space_id: str,
    ) -> IndexGenerationMember:
        if (
            type(row["embedding_id"]) is not int
            or row["embedding_id"] < 1
            or not isinstance(row["document_id"], str)
            or not isinstance(row["object_id"], str)
            or not row["object_id"].startswith("embedding:")
            or len(row["object_id"]) != 74
            or type(row["row_offset"]) is not int
            or row["row_offset"] < 0
            or not isinstance(row["input_fingerprint"], str)
            or row["input_fingerprint"]
            != EmbeddingBatchRules._input_fingerprint(
                row["document_id"],
                space_id,
            )
            or type(row["sequence_no"]) is not int
            or row["sequence_no"] < 1
        ):
            raise IndexGenerationError("embedding_membership_conflict")

        digest = row["object_id"].removeprefix("embedding:")
        expected_object = (
            digest,
            f"objects/embedding/{digest[:2]}/{digest}",
            "embedding",
            "application/x-npy",
            "available",
            "embedding-vector-batch-v1",
        )
        actual_object = (
            row["content_sha256"],
            row["relative_path"],
            row["kind"],
            row["media_type"],
            row["object_state"],
            row["retention_policy"],
        )
        if actual_object != expected_object:
            raise IndexGenerationError("embedding_object_unavailable")
        if type(row["byte_size"]) is not int or row["byte_size"] < 1:
            raise IndexGenerationError("embedding_object_unavailable")
        return IndexGenerationMember(
            row["embedding_id"],
            row["document_id"],
            row["object_id"],
            row["row_offset"],
            row["input_fingerprint"],
            row["sequence_no"],
        )

    @classmethod
    def _snapshot(
        cls,
        connection: sqlite3.Connection,
        space_id: str,
    ) -> IndexGenerationSnapshot:
        space = cls._space(connection, space_id)
        documents = connection.execute(
            "SELECT id,sequence_no FROM search_documents "
            "WHERE is_current=1 ORDER BY sequence_no,id"
        ).fetchall()
        if not documents:
            raise IndexGenerationError("embedding_coverage_incomplete")

        rows = connection.execute(
            "SELECT d.id AS document_id,d.sequence_no,"
            "e.id AS embedding_id,e.object_id,e.row_offset,e.input_fingerprint,"
            "o.content_sha256,o.relative_path,o.kind,o.media_type,o.byte_size,"
            "o.state AS object_state,o.retention_policy "
            "FROM search_documents d "
            "LEFT JOIN embeddings e ON e.document_id=d.id AND e.space_id=? "
            "LEFT JOIN object_registry o ON o.object_id=e.object_id "
            "WHERE d.is_current=1 "
            "ORDER BY d.sequence_no,d.id,e.id",
            (space_id,),
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["document_id"], []).append(row)

        members = []
        for document in documents:
            matches = grouped.get(document["id"], [])
            if len(matches) == 0 or matches[0]["embedding_id"] is None:
                raise IndexGenerationError("embedding_coverage_incomplete")
            if len(matches) != 1:
                raise IndexGenerationError("embedding_membership_conflict")
            members.append(cls._member(matches[0], space_id=space_id))

        members.sort(key=lambda item: item.embedding_id)
        snapshot = IndexGenerationSnapshot(
            space["id"],
            space["configuration_fingerprint"],
            space["dimension"],
            space["dtype"],
            space["metric"],
            max(row["sequence_no"] for row in documents),
            len(members),
            tuple(members),
        )
        IndexGenerationRules.validate_snapshot(snapshot)
        return snapshot

    @staticmethod
    def _result(
        row: sqlite3.Row,
        *,
        replayed: bool,
    ) -> PersistedIndexGeneration:
        try:
            created_at = IndexGenerationRules.instant(
                datetime.fromisoformat(row["created_at"])
            )
        except (
            ValueError,
            TypeError,
            OverflowError,
            IndexGenerationError,
        ) as exc:
            raise IndexGenerationError("index_generation_state_corrupt") from exc
        if row["state"] not in {"building", "ready", "failed"}:
            raise IndexGenerationError("index_generation_state_corrupt")
        return PersistedIndexGeneration(
            row["id"],
            row["space_id"],
            row["state"],
            row["relative_directory"],
            row["membership_digest"],
            row["document_high_watermark"],
            row["vector_count"],
            created_at,
            replayed,
        )

    @staticmethod
    def _expected(
        generation: PreparedIndexGeneration,
    ) -> tuple[object, ...]:
        return (
            generation.generation_id,
            generation.space_id,
            generation.relative_directory,
            generation.membership_digest,
            generation.document_high_watermark,
            generation.vector_count,
        )

    @classmethod
    def _existing(
        cls,
        connection: sqlite3.Connection,
        generation: PreparedIndexGeneration,
    ) -> sqlite3.Row | None:
        rows = connection.execute(
            "SELECT * FROM index_generations "
            "WHERE id=? OR relative_directory=?",
            (
                generation.generation_id,
                generation.relative_directory,
            ),
        ).fetchall()
        if len(rows) > 1:
            raise IndexGenerationError("index_generation_state_corrupt")
        if not rows:
            return None
        row = rows[0]
        actual = (
            row["id"],
            row["space_id"],
            row["relative_directory"],
            row["membership_digest"],
            row["document_high_watermark"],
            row["vector_count"],
        )
        if actual != cls._expected(generation):
            raise IndexGenerationError("index_generation_conflict")
        members = connection.execute(
            "SELECT embedding_id,space_id FROM index_generation_members "
            "WHERE generation_id=? ORDER BY embedding_id",
            (generation.generation_id,),
        ).fetchall()
        expected_members = [
            (member.embedding_id, generation.space_id)
            for member in generation.members
        ]
        if [tuple(member) for member in members] != expected_members:
            raise IndexGenerationError("index_generation_conflict")
        return row

    def snapshot(self, space_id: str) -> IndexGenerationSnapshot:
        if not isinstance(space_id, str):
            raise IndexGenerationError("invalid_index_generation")
        with self._transaction(write=False) as connection:
            return self._snapshot(connection, space_id)

    def start(
        self,
        generation: PreparedIndexGeneration,
        *,
        created_at: datetime,
    ) -> PersistedIndexGeneration:
        IndexGenerationRules.validate_prepared(generation)
        created = IndexGenerationRules.instant(created_at)
        with self._transaction(write=True) as connection:
            current = self._snapshot(connection, generation.space_id)
            expected_snapshot = IndexGenerationSnapshot(
                generation.space_id,
                generation.space_configuration_fingerprint,
                generation.dimension,
                generation.dtype,
                generation.metric,
                generation.document_high_watermark,
                generation.vector_count,
                generation.members,
            )
            if current != expected_snapshot:
                raise IndexGenerationError("index_generation_snapshot_changed")

            existing = self._existing(connection, generation)
            if existing is not None:
                return self._result(existing, replayed=True)

            inserted = connection.execute(
                "INSERT INTO index_generations("
                "id,space_id,state,relative_directory,manifest_sha256,"
                "index_sha256,membership_digest,document_high_watermark,"
                "vector_count,created_at,verified_at"
                ") VALUES(?,?,'building',?,NULL,NULL,?,?,?,?,NULL)",
                (
                    generation.generation_id,
                    generation.space_id,
                    generation.relative_directory,
                    generation.membership_digest,
                    generation.document_high_watermark,
                    generation.vector_count,
                    created.isoformat(),
                ),
            ).rowcount
            if inserted != 1:
                raise IndexGenerationError("index_generation_database_conflict")
            for member in generation.members:
                if connection.execute(
                    "INSERT INTO index_generation_members("
                    "generation_id,embedding_id,space_id"
                    ") VALUES(?,?,?)",
                    (
                        generation.generation_id,
                        member.embedding_id,
                        generation.space_id,
                    ),
                ).rowcount != 1:
                    raise IndexGenerationError(
                        "index_generation_database_conflict"
                    )

            row = connection.execute(
                "SELECT * FROM index_generations WHERE id=?",
                (generation.generation_id,),
            ).fetchone()
            if row is None:
                raise IndexGenerationError("index_generation_state_corrupt")
            if self._existing(connection, generation) is None:
                raise IndexGenerationError("index_generation_state_corrupt")
            return self._result(row, replayed=False)
