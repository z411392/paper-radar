import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.research_workflow.dtos.source_catalog_projection import (
    SourceCatalogProjectionProgress,
)
from libs.research_workflow.exceptions.source_catalog_projection_error import (
    SourceCatalogProjectionError as Error,
)


class SqliteSourceCatalogProjectionStoreAdapter:
    _SOURCES = frozenset({"arxiv", "pubmed"})

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _unit_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"unit:[0-9a-f]{64}", value) is None
        ):
            raise Error("invalid_source_catalog_projection_unit")
        return value

    @staticmethod
    def _observation_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"observation:[0-9a-f]{64}", value) is None
        ):
            raise Error("invalid_source_catalog_projection_observation")
        return value

    @staticmethod
    def _instant(value: object) -> str:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise Error("invalid_source_catalog_projection_time")
        try:
            return value.astimezone(timezone.utc).isoformat()
        except (ValueError, OverflowError):
            raise Error("invalid_source_catalog_projection_time") from None

    @staticmethod
    def _decode_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise Error("source_catalog_projection_state_corrupt")
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise Error("source_catalog_projection_state_corrupt") from None
        if result.tzinfo is None or result.utcoffset() is None:
            raise Error("source_catalog_projection_state_corrupt")
        return result.astimezone(timezone.utc)

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise Error("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise Error("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except Error:
            raise
        except sqlite3.IntegrityError as exc:
            raise Error("source_catalog_projection_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "source_catalog_projection_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "source_catalog_projection_database_error"
            )
            raise Error(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _row(cls, row: sqlite3.Row) -> SourceCatalogProjectionProgress:
        last = row["last_observation_id"]
        if last is not None:
            cls._observation_id(last)
        if (
            row["source"] not in cls._SOURCES
            or type(row["projected_count"]) is not int
            or row["projected_count"] < 0
            or row["state"] not in {"running", "succeeded"}
            or type(row["checkpoint_version"]) is not int
            or row["checkpoint_version"] < 0
            or (row["projected_count"] == 0) != (last is None)
        ):
            raise Error("source_catalog_projection_state_corrupt")
        return SourceCatalogProjectionProgress(
            row["unit_id"],
            row["source"],
            last,
            row["projected_count"],
            row["state"],
            row["checkpoint_version"],
            cls._decode_time(row["created_at"]),
            cls._decode_time(row["updated_at"]),
        )

    def ensure(
        self,
        unit_id: str,
        source: str,
        created_at: datetime,
    ) -> SourceCatalogProjectionProgress:
        unit_id = self._unit_id(unit_id)
        if source not in self._SOURCES:
            raise Error("unsupported_source_catalog_projection_source")
        created = self._instant(created_at)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM source_catalog_projection_progress "
                "WHERE unit_id=? AND source=?",
                (unit_id, source),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO source_catalog_projection_progress("
                    "unit_id,source,last_observation_id,projected_count,state,"
                    "checkpoint_version,created_at,updated_at"
                    ") VALUES(?,?,NULL,0,'running',0,?,?)",
                    (unit_id, source, created, created),
                )
                row = connection.execute(
                    "SELECT * FROM source_catalog_projection_progress "
                    "WHERE unit_id=? AND source=?",
                    (unit_id, source),
                ).fetchone()
            assert row is not None
            return self._row(row)

    def advance(
        self,
        unit_id: str,
        source: str,
        *,
        expected_checkpoint_version: int,
        expected_after_observation_id: str | None,
        observation_id: str,
        updated_at: datetime,
    ) -> SourceCatalogProjectionProgress:
        unit_id = self._unit_id(unit_id)
        observation_id = self._observation_id(observation_id)
        if source not in self._SOURCES:
            raise Error("unsupported_source_catalog_projection_source")
        if (
            type(expected_checkpoint_version) is not int
            or expected_checkpoint_version < 0
        ):
            raise Error("invalid_source_catalog_projection_checkpoint")
        if expected_after_observation_id is not None:
            expected_after_observation_id = self._observation_id(
                expected_after_observation_id
            )
        updated = self._instant(updated_at)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM source_catalog_projection_progress "
                "WHERE unit_id=? AND source=?",
                (unit_id, source),
            ).fetchone()
            if row is None:
                raise Error("source_catalog_projection_missing")
            state = self._row(row)
            if state.state != "running":
                raise Error("source_catalog_projection_already_complete")
            if (
                state.checkpoint_version != expected_checkpoint_version
                or state.last_observation_id != expected_after_observation_id
                or (
                    state.last_observation_id is not None
                    and observation_id <= state.last_observation_id
                )
            ):
                raise Error("source_catalog_projection_checkpoint_conflict")
            changed = connection.execute(
                "UPDATE source_catalog_projection_progress SET "
                "last_observation_id=?,projected_count=projected_count+1,"
                "checkpoint_version=checkpoint_version+1,updated_at=? "
                "WHERE unit_id=? AND source=? AND state='running' "
                "AND checkpoint_version=?",
                (
                    observation_id,
                    updated,
                    unit_id,
                    source,
                    expected_checkpoint_version,
                ),
            ).rowcount
            if changed != 1:
                raise Error("source_catalog_projection_checkpoint_conflict")
            updated_row = connection.execute(
                "SELECT * FROM source_catalog_projection_progress "
                "WHERE unit_id=? AND source=?",
                (unit_id, source),
            ).fetchone()
            assert updated_row is not None
            return self._row(updated_row)

    def complete(
        self,
        unit_id: str,
        source: str,
        *,
        expected_checkpoint_version: int,
        updated_at: datetime,
    ) -> SourceCatalogProjectionProgress:
        unit_id = self._unit_id(unit_id)
        if source not in self._SOURCES:
            raise Error("unsupported_source_catalog_projection_source")
        if (
            type(expected_checkpoint_version) is not int
            or expected_checkpoint_version < 0
        ):
            raise Error("invalid_source_catalog_projection_checkpoint")
        updated = self._instant(updated_at)
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM source_catalog_projection_progress "
                "WHERE unit_id=? AND source=?",
                (unit_id, source),
            ).fetchone()
            if row is None:
                raise Error("source_catalog_projection_missing")
            state = self._row(row)
            if state.state == "succeeded":
                return state
            if state.checkpoint_version != expected_checkpoint_version:
                raise Error("source_catalog_projection_checkpoint_conflict")
            changed = connection.execute(
                "UPDATE source_catalog_projection_progress SET state='succeeded',"
                "checkpoint_version=checkpoint_version+1,updated_at=? "
                "WHERE unit_id=? AND source=? AND state='running' "
                "AND checkpoint_version=?",
                (
                    updated,
                    unit_id,
                    source,
                    expected_checkpoint_version,
                ),
            ).rowcount
            if changed != 1:
                raise Error("source_catalog_projection_checkpoint_conflict")
            updated_row = connection.execute(
                "SELECT * FROM source_catalog_projection_progress "
                "WHERE unit_id=? AND source=?",
                (unit_id, source),
            ).fetchone()
            assert updated_row is not None
            return self._row(updated_row)
