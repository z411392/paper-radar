import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.delivery.exceptions.prior_recipient_error import PriorRecipientError


class SqlitePriorRecipientHistoryAdapter:
    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        *,
        maximum_alias_family: int = 10000,
    ) -> None:
        if (
            type(maximum_alias_family) is not int
            or not 1 <= maximum_alias_family <= 100000
        ):
            raise PriorRecipientError("invalid_prior_recipient_alias_limit")
        self._connect = connect
        self._maximum_alias_family = maximum_alias_family

    @staticmethod
    def _text(value: object, code: str, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\0" in value
        ):
            raise PriorRecipientError(code)
        try:
            if len(value.encode("utf-8")) > maximum:
                raise PriorRecipientError(code)
        except UnicodeEncodeError:
            raise PriorRecipientError(code) from None
        return value

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise PriorRecipientError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise PriorRecipientError("foreign_keys_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except PriorRecipientError:
            raise
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "prior_recipient_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "prior_recipient_database_error"
            )
            raise PriorRecipientError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _require_work(
        connection: sqlite3.Connection,
        work_id: str,
    ) -> None:
        if connection.execute(
            "SELECT 1 FROM paper_works WHERE id=?",
            (work_id,),
        ).fetchone() is None:
            raise PriorRecipientError("prior_recipient_work_missing")

    @classmethod
    def _canonical_work(
        cls,
        connection: sqlite3.Connection,
        work_id: str,
    ) -> str:
        current = work_id
        visited: set[str] = set()
        for _ in range(128):
            cls._require_work(connection, current)
            if current in visited:
                raise PriorRecipientError("prior_recipient_alias_cycle")
            visited.add(current)
            row = connection.execute(
                "SELECT canonical_work_id FROM work_aliases "
                "WHERE alias_work_id=?",
                (current,),
            ).fetchone()
            if row is None:
                return current
            target = row["canonical_work_id"]
            if not isinstance(target, str) or not target:
                raise PriorRecipientError("prior_recipient_alias_corrupt")
            current = target
        raise PriorRecipientError("prior_recipient_alias_depth_exceeded")

    def _family(
        self,
        connection: sqlite3.Connection,
        canonical_work_id: str,
    ) -> tuple[str, ...]:
        family = {canonical_work_id}
        frontier = [canonical_work_id]
        while frontier:
            discovered: list[str] = []
            for offset in range(0, len(frontier), 400):
                chunk = frontier[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT alias_work_id,canonical_work_id FROM work_aliases "
                    f"WHERE canonical_work_id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    alias = row["alias_work_id"]
                    target = row["canonical_work_id"]
                    if (
                        not isinstance(alias, str)
                        or not alias
                        or target not in family
                    ):
                        raise PriorRecipientError(
                            "prior_recipient_alias_corrupt"
                        )
                    self._require_work(connection, alias)
                    if alias in family:
                        raise PriorRecipientError(
                            "prior_recipient_alias_cycle"
                        )
                    family.add(alias)
                    discovered.append(alias)
                    if len(family) > self._maximum_alias_family:
                        raise PriorRecipientError(
                            "prior_recipient_alias_limit_exceeded"
                        )
            frontier = discovered
        return tuple(sorted(family))

    def contains(
        self,
        reader_id: str,
        channel: str,
        work_id: str,
    ) -> bool:
        reader = self._text(
            reader_id,
            "invalid_prior_recipient_reader",
            512,
        )
        work = self._text(
            work_id,
            "invalid_prior_recipient_work",
            256,
        )
        if channel not in {"email", "rss"}:
            raise PriorRecipientError("invalid_prior_recipient_channel")
        with self._transaction() as connection:
            canonical = self._canonical_work(connection, work)
            family = self._family(connection, canonical)
            for offset in range(0, len(family), 400):
                chunk = family[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                row = connection.execute(
                    "SELECT 1 FROM notification_ledger n "
                    "JOIN research_events e ON e.id=n.event_id "
                    "WHERE n.reader_id=? AND n.channel=? "
                    "AND n.state IN ('accepted','unknown') "
                    f"AND e.work_id IN ({placeholders}) LIMIT 1",
                    (reader, channel, *chunk),
                ).fetchone()
                if row is not None:
                    return True
            return False
