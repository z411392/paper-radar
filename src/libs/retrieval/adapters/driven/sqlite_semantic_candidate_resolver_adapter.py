import math
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.search_hybrid import (
    SearchResolvedSemanticHit,
    SearchSemanticHit,
)
from libs.retrieval.exceptions.search_query_error import SearchQueryError


class SqliteSemanticCandidateResolverAdapter:
    _SPACE = re.compile(r"embspace:[0-9a-f]{64}")
    _GENERATION = re.compile(r"faissgen:[0-9a-f]{64}")
    _HASH = re.compile(r"[0-9a-f]{64}")
    _CHUNK = 500

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise SearchQueryError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except SearchQueryError:
            raise
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "search_query_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "search_query_database_error"
            )
            raise SearchQueryError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _validate(
        cls,
        pin: ActiveIndexPin,
        hits: tuple[SearchSemanticHit, ...],
    ) -> None:
        if (
            not isinstance(pin, ActiveIndexPin)
            or cls._SPACE.fullmatch(pin.space_id) is None
            or cls._GENERATION.fullmatch(pin.generation_id) is None
            or type(pin.pointer_version) is not int
            or pin.pointer_version < 1
            or cls._HASH.fullmatch(pin.index_sha256) is None
            or cls._HASH.fullmatch(pin.manifest_sha256) is None
            or cls._HASH.fullmatch(pin.membership_digest) is None
            or not isinstance(hits, tuple)
            or len(hits) > 10_000
        ):
            raise SearchQueryError("invalid_semantic_candidates")
        seen = set()
        for hit in hits:
            if (
                not isinstance(hit, SearchSemanticHit)
                or type(hit.embedding_id) is not int
                or hit.embedding_id < 1
                or hit.embedding_id in seen
                or isinstance(hit.raw_score, bool)
                or not isinstance(hit.raw_score, (int, float))
                or not math.isfinite(float(hit.raw_score))
            ):
                raise SearchQueryError("invalid_semantic_candidates")
            seen.add(hit.embedding_id)

    @staticmethod
    def _generation(
        connection: sqlite3.Connection,
        pin: ActiveIndexPin,
    ) -> None:
        row = connection.execute(
            "SELECT * FROM index_generations WHERE id=?",
            (pin.generation_id,),
        ).fetchone()
        if row is None or (
            row["space_id"],
            row["state"],
            row["relative_directory"],
            row["index_sha256"],
            row["manifest_sha256"],
            row["membership_digest"],
            row["document_high_watermark"],
            row["vector_count"],
        ) != (
            pin.space_id,
            "ready",
            pin.relative_directory,
            pin.index_sha256,
            pin.manifest_sha256,
            pin.membership_digest,
            pin.document_high_watermark,
            pin.vector_count,
        ):
            raise SearchQueryError("semantic_generation_changed")

    def resolve(
        self,
        pin: ActiveIndexPin,
        hits: tuple[SearchSemanticHit, ...],
    ) -> tuple[SearchResolvedSemanticHit, ...]:
        self._validate(pin, hits)
        if not hits:
            return ()
        expected_ids = tuple(hit.embedding_id for hit in hits)
        found: dict[int, sqlite3.Row] = {}
        with self._transaction() as connection:
            self._generation(connection, pin)
            for start in range(0, len(expected_ids), self._CHUNK):
                chunk = expected_ids[start : start + self._CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT m.embedding_id,e.document_id,d.work_id,"
                    "d.revision_id,d.projection_kind,d.is_current "
                    "FROM index_generation_members m "
                    "JOIN embeddings e ON e.id=m.embedding_id "
                    "AND e.space_id=m.space_id "
                    "JOIN search_documents d ON d.id=e.document_id "
                    "WHERE m.generation_id=? AND m.space_id=? "
                    "AND m.embedding_id IN ("
                    + placeholders
                    + ")",
                    (pin.generation_id, pin.space_id, *chunk),
                ).fetchall()
                for row in rows:
                    embedding_id = row["embedding_id"]
                    if embedding_id in found:
                        raise SearchQueryError(
                            "semantic_index_mapping_mismatch"
                        )
                    found[embedding_id] = row
            if set(found) != set(expected_ids):
                raise SearchQueryError("semantic_index_mapping_mismatch")

            resolved = []
            scores = {hit.embedding_id: float(hit.raw_score) for hit in hits}
            for embedding_id in expected_ids:
                row = found[embedding_id]
                if row["is_current"] != 1:
                    continue
                values = (
                    row["document_id"],
                    row["work_id"],
                    row["revision_id"],
                    row["projection_kind"],
                )
                if any(not isinstance(value, str) or not value for value in values):
                    raise SearchQueryError("search_query_state_corrupt")
                resolved.append(
                    SearchResolvedSemanticHit(
                        embedding_id,
                        *values,
                        scores[embedding_id],
                    )
                )
            return tuple(resolved)
