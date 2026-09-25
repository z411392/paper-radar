import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from libs.delivery.dtos.local_reading_history import (
    LocalDeliveryAttemptView,
    LocalNotificationView,
)
from libs.delivery.exceptions.local_reading_history_error import (
    LocalReadingHistoryError,
)


class SqliteLocalDeliveryHistoryAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _text(value: object, code: str, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\0" in value
        ):
            raise LocalReadingHistoryError(code)
        try:
            if len(value.encode("utf-8")) > maximum:
                raise LocalReadingHistoryError(code)
        except UnicodeEncodeError:
            raise LocalReadingHistoryError(code) from None
        return value

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise LocalReadingHistoryError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise LocalReadingHistoryError("foreign_keys_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except LocalReadingHistoryError:
            raise
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "local_reading_database_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "local_reading_database_error"
            )
            raise LocalReadingHistoryError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    def read(
        self,
        reader_id: str,
        work_ids: tuple[str, ...],
        channel: str | None,
    ) -> tuple[LocalNotificationView, ...]:
        reader = self._text(
            reader_id,
            "invalid_local_reading_reader",
            512,
        )
        if (
            not isinstance(work_ids, tuple)
            or not work_ids
            or len(work_ids) > 100000
        ):
            raise LocalReadingHistoryError("invalid_local_reading_work_family")
        family = tuple(
            self._text(
                work_id,
                "invalid_local_reading_work_family",
                256,
            )
            for work_id in work_ids
        )
        if len(family) != len(set(family)):
            raise LocalReadingHistoryError("invalid_local_reading_work_family")
        if channel is not None and channel not in {"email", "rss"}:
            raise LocalReadingHistoryError("invalid_local_reading_channel")

        with self._transaction() as connection:
            rows: list[sqlite3.Row] = []
            for offset in range(0, len(family), 350):
                chunk = family[offset : offset + 350]
                placeholders = ",".join("?" for _ in chunk)
                parameters: tuple[object, ...] = (reader, *chunk)
                channel_clause = ""
                if channel is not None:
                    channel_clause = "AND n.channel=? "
                    parameters = (*parameters, channel)
                rows.extend(
                    connection.execute(
                        "SELECT n.id AS ledger_id,n.event_id,n.channel,"
                        "n.state AS ledger_state,n.created_at AS ledger_created_at,"
                        "e.event_kind,e.work_id,d.id AS digest_id,d.period_key,"
                        "di.item_kind,di.summary_id,di.revision_id,"
                        "o.id AS outbox_id,o.state AS outbox_state "
                        "FROM notification_ledger n "
                        "JOIN research_events e ON e.id=n.event_id "
                        "JOIN delivery_outbox o ON o.id=n.outbox_id "
                        "JOIN digests d ON d.id=o.digest_id "
                        "LEFT JOIN digest_items di "
                        "ON di.digest_id=d.id AND di.event_id=n.event_id "
                        "WHERE n.reader_id=? "
                        f"AND e.work_id IN ({placeholders}) "
                        f"{channel_clause}",
                        parameters,
                    ).fetchall()
                )

            rows.sort(
                key=lambda row: (
                    row["ledger_created_at"],
                    row["event_id"],
                    row["channel"],
                )
            )
            outbox_ids = tuple(sorted({row["outbox_id"] for row in rows}))
            attempts: dict[str, list[sqlite3.Row]] = {}
            for offset in range(0, len(outbox_ids), 400):
                chunk = outbox_ids[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                for row in connection.execute(
                    "SELECT * FROM delivery_attempts "
                    f"WHERE outbox_id IN ({placeholders}) "
                    "ORDER BY outbox_id,attempt_no",
                    tuple(chunk),
                ).fetchall():
                    attempts.setdefault(row["outbox_id"], []).append(row)

            result = []
            for row in rows:
                if row["item_kind"] not in {"paper", "status_notice"}:
                    raise LocalReadingHistoryError(
                        "local_reading_history_corrupt"
                    )
                if row["ledger_state"] not in {
                    "reserved",
                    "accepted",
                    "unknown",
                    "cancelled",
                }:
                    raise LocalReadingHistoryError(
                        "local_reading_history_corrupt"
                    )
                if row["outbox_state"] not in {
                    "pending",
                    "sending",
                    "provider_accepted",
                    "failed",
                    "unknown",
                    "cancelled",
                }:
                    raise LocalReadingHistoryError(
                        "local_reading_history_corrupt"
                    )
                result.append(
                    LocalNotificationView(
                        row["ledger_id"],
                        row["event_id"],
                        row["event_kind"],
                        row["work_id"],
                        row["channel"],
                        row["ledger_state"],
                        row["ledger_created_at"],
                        row["digest_id"],
                        row["period_key"],
                        row["item_kind"],
                        row["summary_id"],
                        row["revision_id"],
                        row["outbox_id"],
                        row["outbox_state"],
                        tuple(
                            LocalDeliveryAttemptView(
                                attempt["id"],
                                attempt["attempt_no"],
                                attempt["state"],
                                attempt["provider_message_id"],
                                attempt["error_code"],
                                attempt["started_at"],
                                attempt["finished_at"],
                            )
                            for attempt in attempts.get(row["outbox_id"], [])
                        ),
                    )
                )
            return tuple(result)
