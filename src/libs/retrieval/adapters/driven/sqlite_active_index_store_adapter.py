import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from libs.retrieval.dtos.active_index import (
    ActivateIndexInput,
    ActiveIndexPin,
)
from libs.retrieval.exceptions.active_index_error import ActiveIndexError


class SqliteActiveIndexStoreAdapter:
    _SPACE = re.compile(r"embspace:[0-9a-f]{64}")
    _GENERATION = re.compile(r"faissgen:[0-9a-f]{64}")
    _HASH = re.compile(r"[0-9a-f]{64}")

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise ActiveIndexError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise ActiveIndexError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except ActiveIndexError:
            raise
        except sqlite3.IntegrityError as exc:
            raise ActiveIndexError("active_index_database_conflict") from exc
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "active_index_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "active_index_database_error"
            )
            raise ActiveIndexError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def _input(cls, value: ActivateIndexInput) -> None:
        if (
            not isinstance(value, ActivateIndexInput)
            or cls._SPACE.fullmatch(value.space_id) is None
            or cls._GENERATION.fullmatch(value.generation_id) is None
        ):
            raise ActiveIndexError("invalid_active_index")
        both_none = (
            value.expected_generation_id is None
            and value.expected_pointer_version is None
        )
        both_set = (
            isinstance(value.expected_generation_id, str)
            and cls._GENERATION.fullmatch(value.expected_generation_id)
            is not None
            and type(value.expected_pointer_version) is int
            and value.expected_pointer_version >= 1
        )
        if not (both_none or both_set):
            raise ActiveIndexError("invalid_active_index")

    @staticmethod
    def _time(value: datetime) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ActiveIndexError("invalid_active_index_time")
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ActiveIndexError("invalid_active_index_time") from None

    @classmethod
    def _generation(
        cls,
        connection: sqlite3.Connection,
        generation_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM index_generations WHERE id=?",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise ActiveIndexError("active_generation_missing")
        if row["state"] != "ready":
            raise ActiveIndexError("active_generation_not_ready")
        if (
            cls._SPACE.fullmatch(row["space_id"]) is None
            or not isinstance(row["relative_directory"], str)
            or not row["relative_directory"]
            or cls._HASH.fullmatch(row["index_sha256"] or "") is None
            or cls._HASH.fullmatch(row["manifest_sha256"] or "") is None
            or cls._HASH.fullmatch(row["membership_digest"] or "") is None
            or type(row["document_high_watermark"]) is not int
            or row["document_high_watermark"] < 1
            or type(row["vector_count"]) is not int
            or row["vector_count"] < 1
            or not isinstance(row["verified_at"], str)
        ):
            raise ActiveIndexError("active_generation_corrupt")
        count = connection.execute(
            "SELECT count(*) FROM index_generation_members "
            "WHERE generation_id=? AND space_id=?",
            (row["id"], row["space_id"]),
        ).fetchone()[0]
        if count != row["vector_count"]:
            raise ActiveIndexError("active_generation_corrupt")
        return row

    @classmethod
    def _pin(
        cls,
        active: sqlite3.Row,
        generation: sqlite3.Row,
        *,
        replayed: bool,
    ) -> ActiveIndexPin:
        if (
            active["space_id"] != generation["space_id"]
            or active["generation_id"] != generation["id"]
            or type(active["pointer_version"]) is not int
            or active["pointer_version"] < 1
        ):
            raise ActiveIndexError("active_index_corrupt")
        try:
            activated_at = cls._time(
                datetime.fromisoformat(active["activated_at"])
            )
        except (
            ValueError,
            TypeError,
            OverflowError,
            ActiveIndexError,
        ) as exc:
            raise ActiveIndexError("active_index_corrupt") from exc
        return ActiveIndexPin(
            active["space_id"],
            active["generation_id"],
            active["pointer_version"],
            generation["relative_directory"],
            generation["index_sha256"],
            generation["manifest_sha256"],
            generation["membership_digest"],
            generation["document_high_watermark"],
            generation["vector_count"],
            activated_at,
            replayed,
        )

    def activate(
        self,
        value: ActivateIndexInput,
        *,
        activated_at: datetime,
    ) -> ActiveIndexPin:
        self._input(value)
        activated = self._time(activated_at)
        with self._transaction(write=True) as connection:
            generation = self._generation(
                connection,
                value.generation_id,
            )
            if generation["space_id"] != value.space_id:
                raise ActiveIndexError(
                    "active_generation_space_mismatch"
                )
            current = connection.execute(
                "SELECT * FROM active_indexes WHERE space_id=?",
                (value.space_id,),
            ).fetchone()
            if current is not None and current["generation_id"] == value.generation_id:
                return self._pin(current, generation, replayed=True)
            if current is None:
                if (
                    value.expected_generation_id is not None
                    or value.expected_pointer_version is not None
                ):
                    raise ActiveIndexError("active_index_stale")
                changed = connection.execute(
                    "INSERT INTO active_indexes("
                    "space_id,generation_id,pointer_version,activated_at"
                    ") VALUES(?,?,1,?)",
                    (
                        value.space_id,
                        value.generation_id,
                        activated.isoformat(),
                    ),
                ).rowcount
            else:
                if (
                    current["generation_id"] != value.expected_generation_id
                    or current["pointer_version"]
                    != value.expected_pointer_version
                ):
                    raise ActiveIndexError("active_index_stale")
                changed = connection.execute(
                    "UPDATE active_indexes SET generation_id=?,"
                    "pointer_version=pointer_version+1,activated_at=? "
                    "WHERE space_id=? AND generation_id=? "
                    "AND pointer_version=?",
                    (
                        value.generation_id,
                        activated.isoformat(),
                        value.space_id,
                        value.expected_generation_id,
                        value.expected_pointer_version,
                    ),
                ).rowcount
            if changed != 1:
                raise ActiveIndexError("active_index_stale")
            written = connection.execute(
                "SELECT * FROM active_indexes WHERE space_id=?",
                (value.space_id,),
            ).fetchone()
            if written is None:
                raise ActiveIndexError("active_index_corrupt")
            current_generation = self._generation(
                connection,
                written["generation_id"],
            )
            return self._pin(
                written,
                current_generation,
                replayed=False,
            )

    def pin(self, space_id: str) -> ActiveIndexPin | None:
        if (
            not isinstance(space_id, str)
            or self._SPACE.fullmatch(space_id) is None
        ):
            raise ActiveIndexError("invalid_active_index")
        with self._transaction(write=False) as connection:
            active = connection.execute(
                "SELECT * FROM active_indexes WHERE space_id=?",
                (space_id,),
            ).fetchone()
            if active is None:
                return None
            generation = self._generation(
                connection,
                active["generation_id"],
            )
            return self._pin(active, generation, replayed=False)
