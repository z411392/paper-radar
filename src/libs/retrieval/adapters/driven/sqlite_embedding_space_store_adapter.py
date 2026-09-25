import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from libs.retrieval.domain.services.embedding_space_rules import EmbeddingSpaceRules
from libs.retrieval.dtos.embedding_space import (
    PreparedEmbeddingSpace,
    RegisteredEmbeddingSpace,
)
from libs.retrieval.exceptions.embedding_space_error import EmbeddingSpaceError


class SqliteEmbeddingSpaceStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise EmbeddingSpaceError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise EmbeddingSpaceError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except EmbeddingSpaceError:
            raise
        except sqlite3.IntegrityError as exc:
            raise EmbeddingSpaceError("embedding_space_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "embedding_space_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "embedding_space_database_error"
            )
            raise EmbeddingSpaceError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _result(
        row: sqlite3.Row,
        *,
        replayed: bool,
    ) -> RegisteredEmbeddingSpace:
        try:
            created_at = EmbeddingSpaceRules.instant(
                datetime.fromisoformat(row["created_at"])
            )
        except (ValueError, TypeError, OverflowError, EmbeddingSpaceError) as exc:
            raise EmbeddingSpaceError("embedding_space_state_corrupt") from exc
        return RegisteredEmbeddingSpace(
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
            created_at,
            replayed,
        )

    @staticmethod
    def _expected(space: PreparedEmbeddingSpace) -> tuple[object, ...]:
        return (
            space.space_id,
            space.provider,
            space.model_name,
            space.model_revision,
            space.dimension,
            space.dtype,
            space.normalization_version,
            space.prefix_config_hash,
            space.metric,
            space.configuration_fingerprint,
        )

    @classmethod
    def _existing(
        cls,
        connection: sqlite3.Connection,
        space: PreparedEmbeddingSpace,
    ) -> sqlite3.Row | None:
        rows = connection.execute(
            "SELECT * FROM embedding_spaces "
            "WHERE id=? OR configuration_fingerprint=?",
            (space.space_id, space.configuration_fingerprint),
        ).fetchall()
        if len(rows) > 1:
            raise EmbeddingSpaceError("embedding_space_state_corrupt")
        if not rows:
            return None
        row = rows[0]
        actual = (
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
        if actual != cls._expected(space):
            raise EmbeddingSpaceError("embedding_space_conflict")
        return row

    def save(
        self,
        space: PreparedEmbeddingSpace,
        *,
        created_at: datetime,
    ) -> RegisteredEmbeddingSpace:
        EmbeddingSpaceRules.validate_prepared(space)
        created = EmbeddingSpaceRules.instant(created_at)
        with self._transaction(write=True) as connection:
            existing = self._existing(connection, space)
            if existing is not None:
                return self._result(existing, replayed=True)
            inserted = connection.execute(
                "INSERT INTO embedding_spaces("
                "id,provider,model_name,model_revision,dimension,dtype,"
                "normalization_version,prefix_config_hash,metric,"
                "configuration_fingerprint,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    space.space_id,
                    space.provider,
                    space.model_name,
                    space.model_revision,
                    space.dimension,
                    space.dtype,
                    space.normalization_version,
                    space.prefix_config_hash,
                    space.metric,
                    space.configuration_fingerprint,
                    created.isoformat(),
                ),
            ).rowcount
            if inserted != 1:
                raise EmbeddingSpaceError("embedding_space_database_conflict")
            row = connection.execute(
                "SELECT * FROM embedding_spaces WHERE id=?",
                (space.space_id,),
            ).fetchone()
            if row is None:
                raise EmbeddingSpaceError("embedding_space_state_corrupt")
            if self._existing(connection, space) is None:
                raise EmbeddingSpaceError("embedding_space_state_corrupt")
            return self._result(row, replayed=False)
