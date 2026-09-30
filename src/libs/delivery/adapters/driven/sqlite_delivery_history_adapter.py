import re
import sqlite3
from collections.abc import Callable

from libs.delivery.dtos.delivery_history import (
    DeliveryHistory,
    DeliveryHistoryItem,
)
from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError


class SqliteDeliveryHistoryAdapter:
    _DELIVERY_STATES = {
        ("pending", "queued"): "pending",
        ("sending", "queued"): "sending",
        ("provider_accepted", "sent"): "sent",
        ("failed", "queued"): "failed",
        ("unknown", "unknown"): "unknown",
        ("cancelled", "cancelled"): "cancelled",
    }

    _ITEM_STATES = {
        ("pending", "reserved"): "pending",
        ("sending", "reserved"): "sending",
        ("provider_accepted", "accepted"): "sent",
        ("failed", "reserved"): "failed",
        ("unknown", "unknown"): "unknown",
        ("cancelled", "cancelled"): "cancelled",
    }

    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _reader_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None
        ):
            raise DeliveryStoreError("invalid_reader_id")
        return value

    @staticmethod
    def _limit(value: object) -> int:
        if type(value) is not int or not 1 <= value <= 100:
            raise DeliveryStoreError("invalid_delivery_history_limit")
        return value

    @classmethod
    def _delivery_state(
        cls,
        outbox_state: object,
        digest_state: object,
    ) -> str:
        state = cls._DELIVERY_STATES.get((outbox_state, digest_state))
        if state is None:
            raise DeliveryStoreError("delivery_history_corrupt")
        return state

    @classmethod
    def _send_status(
        cls,
        outbox_state: object,
        notification_state: object,
    ) -> str:
        state = cls._ITEM_STATES.get((outbox_state, notification_state))
        if state is None:
            raise DeliveryStoreError("delivery_history_corrupt")
        return state

    def __call__(
        self,
        reader_id: str,
        *,
        limit: int = 10,
    ) -> tuple[DeliveryHistory, ...]:
        reader = self._reader_id(reader_id)
        maximum = self._limit(limit)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise DeliveryStoreError("owned_connection_required")
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT d.id AS digest_id,o.id AS outbox_id,d.period_key,"
                "d.state AS digest_state,o.state AS outbox_state,"
                "(SELECT COUNT(*) FROM delivery_attempts a "
                "WHERE a.outbox_id=o.id) AS attempt_count,"
                "(SELECT a.error_code FROM delivery_attempts a "
                "WHERE a.outbox_id=o.id ORDER BY a.attempt_no DESC LIMIT 1) "
                "AS latest_error_code "
                "FROM delivery_outbox o "
                "JOIN digests d ON d.id=o.digest_id "
                "JOIN delivery_subscriptions s ON s.id=d.subscription_id "
                "WHERE s.reader_id=? AND s.channel='email' "
                "ORDER BY d.created_at DESC,d.id DESC LIMIT ?",
                (reader, maximum),
            ).fetchall()

            result: list[DeliveryHistory] = []
            for row in rows:
                if (
                    not isinstance(row["digest_id"], str)
                    or not row["digest_id"]
                    or not isinstance(row["outbox_id"], str)
                    or not row["outbox_id"]
                    or not isinstance(row["period_key"], str)
                    or not row["period_key"]
                    or type(row["attempt_count"]) is not int
                    or row["attempt_count"] < 0
                    or (
                        row["latest_error_code"] is not None
                        and not isinstance(row["latest_error_code"], str)
                    )
                ):
                    raise DeliveryStoreError("delivery_history_corrupt")
                delivery_state = self._delivery_state(
                    row["outbox_state"],
                    row["digest_state"],
                )
                item_rows = connection.execute(
                    "SELECT i.position,i.event_id,i.work_id,e.event_kind,"
                    "COALESCE(r.title,w.canonical_title) AS title,"
                    "n.state AS notification_state "
                    "FROM digest_items i "
                    "JOIN research_events e "
                    "ON e.id=i.event_id AND e.work_id=i.work_id "
                    "JOIN paper_works w ON w.id=i.work_id "
                    "LEFT JOIN paper_revisions r "
                    "ON r.id=i.revision_id AND r.work_id=i.work_id "
                    "LEFT JOIN notification_ledger n "
                    "ON n.reader_id=? AND n.channel='email' "
                    "AND n.event_id=i.event_id AND n.outbox_id=? "
                    "WHERE i.digest_id=? ORDER BY i.position",
                    (
                        reader,
                        row["outbox_id"],
                        row["digest_id"],
                    ),
                ).fetchall()
                if not item_rows:
                    raise DeliveryStoreError("delivery_history_corrupt")
                items: list[DeliveryHistoryItem] = []
                for item in item_rows:
                    values = (
                        item["event_id"],
                        item["work_id"],
                        item["title"],
                        item["event_kind"],
                        item["notification_state"],
                    )
                    if any(
                        not isinstance(value, str) or not value
                        for value in values
                    ):
                        raise DeliveryStoreError("delivery_history_corrupt")
                    items.append(
                        DeliveryHistoryItem(
                            event_id=item["event_id"],
                            work_id=item["work_id"],
                            title=item["title"],
                            event_kind=item["event_kind"],
                            notification_state=item["notification_state"],
                            send_status=self._send_status(
                                row["outbox_state"],
                                item["notification_state"],
                            ),
                        )
                    )
                result.append(
                    DeliveryHistory(
                        digest_id=row["digest_id"],
                        outbox_id=row["outbox_id"],
                        period_key=row["period_key"],
                        delivery_state=delivery_state,
                        attempt_count=row["attempt_count"],
                        latest_error_code=row["latest_error_code"],
                        items=tuple(items),
                    )
                )
            connection.commit()
            return tuple(result)
        except DeliveryStoreError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise DeliveryStoreError(
                "delivery_history_database_error"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
