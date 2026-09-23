import sqlite3
from collections.abc import Callable

from libs.delivery.dtos.scheduled_digest import DigestSubscriptionContext
from libs.delivery.exceptions.scheduled_digest_error import ScheduledDigestError


class SqliteDigestDeliveryContextAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _text(value: object, code: str) -> str:
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise ScheduledDigestError(code)
        return value

    def load(self, subscription_id: str) -> DigestSubscriptionContext:
        subscription_id = self._text(subscription_id, "invalid_subscription_id")
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise ScheduledDigestError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT s.id,s.reader_id,s.channel,s.enabled,s.max_items,w.epoch "
                "FROM delivery_subscriptions s "
                "JOIN workspace_metadata w ON w.singleton=1 "
                "WHERE s.id=?",
                (subscription_id,),
            ).fetchone()
            connection.commit()
        except ScheduledDigestError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise ScheduledDigestError("digest_context_database_error") from exc
        finally:
            if connection is not None:
                connection.close()
        if row is None:
            raise ScheduledDigestError("delivery_subscription_missing")
        if (
            not isinstance(row["reader_id"], str)
            or not row["reader_id"]
            or row["channel"] not in {"email", "rss"}
            or type(row["enabled"]) is not int
            or row["enabled"] not in {0, 1}
            or type(row["max_items"]) is not int
            or not 1 <= row["max_items"] <= 100
            or type(row["epoch"]) is not int
            or row["epoch"] < 1
        ):
            raise ScheduledDigestError("digest_context_corrupt")
        return DigestSubscriptionContext(
            row["id"],
            row["reader_id"],
            row["channel"],
            bool(row["enabled"]),
            row["max_items"],
            row["epoch"],
        )

    def already_notified(
        self,
        reader_id: str,
        channel: str,
        event_ids: tuple[str, ...],
    ) -> frozenset[str]:
        reader_id = self._text(reader_id, "invalid_digest_reader")
        if channel not in {"email", "rss"}:
            raise ScheduledDigestError("invalid_delivery_channel")
        if not isinstance(event_ids, tuple) or len(event_ids) > 10000:
            raise ScheduledDigestError("invalid_digest_events")
        if not event_ids:
            return frozenset()
        for event_id in event_ids:
            self._text(event_id, "invalid_digest_event")
        found: set[str] = set()
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            for offset in range(0, len(event_ids), 400):
                chunk = event_ids[offset : offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    "SELECT event_id FROM notification_ledger "
                    "WHERE reader_id=? AND channel=? "
                    f"AND event_id IN ({placeholders})",
                    (reader_id, channel, *chunk),
                ).fetchall()
                found.update(row["event_id"] for row in rows)
            connection.commit()
            return frozenset(found)
        except ScheduledDigestError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise ScheduledDigestError("digest_context_database_error") from exc
        finally:
            if connection is not None:
                connection.close()
