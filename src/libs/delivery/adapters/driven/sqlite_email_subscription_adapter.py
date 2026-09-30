import json
import re
import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from libs.delivery.dtos.email_subscription import EmailSubscription
from libs.delivery.exceptions.delivery_subscription_error import (
    DeliverySubscriptionError,
)


class SqliteEmailSubscriptionAdapter:
    """Create or update the one daily email subscription for a reader."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._connect = connect
        self._now = now

    @staticmethod
    def _reader_id(value: str) -> str:
        if not isinstance(value, str) or re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}",
            value,
        ) is None:
            raise DeliverySubscriptionError("invalid_reader_id")
        return value

    @staticmethod
    def _recipient_ref(value: str) -> str:
        if not isinstance(value, str) or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",
            value,
        ) is None:
            raise DeliverySubscriptionError("invalid_recipient_ref")
        return value

    @staticmethod
    def _timezone(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or "\0" in value
        ):
            raise DeliverySubscriptionError("invalid_delivery_timezone")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise DeliverySubscriptionError("invalid_delivery_timezone") from None
        return value

    @staticmethod
    def _local_time(value: str) -> str:
        if not isinstance(value, str) or re.fullmatch(
            r"(?:[01][0-9]|2[0-3]):[0-5][0-9]",
            value,
        ) is None:
            raise DeliverySubscriptionError("invalid_delivery_schedule")
        return value

    @staticmethod
    def _max_items(value: int) -> int:
        if type(value) is not int or not 1 <= value <= 100:
            raise DeliverySubscriptionError("invalid_max_items")
        return value

    @staticmethod
    def _result(row: sqlite3.Row) -> EmailSubscription:
        try:
            schedule = json.loads(row["schedule_json"])
        except (TypeError, ValueError):
            raise DeliverySubscriptionError("delivery_subscription_corrupt") from None
        if (
            not isinstance(schedule, dict)
            or set(schedule) != {"kind", "local_time"}
            or schedule["kind"] != "daily"
            or not isinstance(schedule["local_time"], str)
        ):
            raise DeliverySubscriptionError("delivery_subscription_corrupt")
        return EmailSubscription(
            row["id"],
            row["reader_id"],
            row["channel"],
            bool(row["enabled"]),
            row["timezone"],
            schedule["local_time"],
            row["max_items"],
            row["recipient_ref"],
            row["policy_version"],
        )

    def __call__(
        self,
        reader_id: str,
        recipient_ref: str,
        timezone_name: str,
        local_time: str,
        *,
        max_items: int = 5,
    ) -> EmailSubscription:
        reader = self._reader_id(reader_id)
        recipient = self._recipient_ref(recipient_ref)
        zone = self._timezone(timezone_name)
        clock = self._local_time(local_time)
        maximum = self._max_items(max_items)
        schedule = json.dumps(
            {"kind": "daily", "local_time": clock},
            sort_keys=True,
            separators=(",", ":"),
        )
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            if connection.in_transaction:
                raise DeliverySubscriptionError("owned_connection_required")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id,reader_id,channel,enabled,timezone,schedule_json,"
                "max_items,recipient_ref,policy_version "
                "FROM delivery_subscriptions "
                "WHERE reader_id=? AND channel='email'",
                (reader,),
            ).fetchone()
            if row is None:
                subscription_id = "subscription:email:" + reader
                connection.execute(
                    "INSERT INTO delivery_subscriptions("
                    "id,reader_id,channel,enabled,timezone,schedule_json,max_items,"
                    "recipient_ref,policy_version,created_at"
                    ") VALUES(?,?,'email',1,?,?,?,?,1,?)",
                    (
                        subscription_id,
                        reader,
                        zone,
                        schedule,
                        maximum,
                        recipient,
                        self._now().astimezone(timezone.utc).isoformat(),
                    ),
                )
            else:
                current = self._result(row)
                changed = (
                    not current.enabled
                    or current.timezone != zone
                    or current.local_time != clock
                    or current.max_items != maximum
                    or current.recipient_ref != recipient
                )
                if changed:
                    if current.policy_version >= 2**63 - 1:
                        raise DeliverySubscriptionError(
                            "delivery_subscription_version_exhausted"
                        )
                    updated = connection.execute(
                        "UPDATE delivery_subscriptions SET enabled=1,timezone=?,"
                        "schedule_json=?,max_items=?,recipient_ref=?,policy_version=? "
                        "WHERE id=? AND policy_version=?",
                        (
                            zone,
                            schedule,
                            maximum,
                            recipient,
                            current.policy_version + 1,
                            current.id,
                            current.policy_version,
                        ),
                    ).rowcount
                    if updated != 1:
                        raise DeliverySubscriptionError(
                            "delivery_subscription_conflict"
                        )
            final = connection.execute(
                "SELECT id,reader_id,channel,enabled,timezone,schedule_json,"
                "max_items,recipient_ref,policy_version "
                "FROM delivery_subscriptions "
                "WHERE reader_id=? AND channel='email'",
                (reader,),
            ).fetchone()
            if final is None:
                raise DeliverySubscriptionError("delivery_subscription_conflict")
            result = self._result(final)
            if (
                not result.enabled
                or result.timezone != zone
                or result.local_time != clock
                or result.max_items != maximum
                or result.recipient_ref != recipient
            ):
                raise DeliverySubscriptionError("delivery_subscription_conflict")
            connection.commit()
            return result
        except DeliverySubscriptionError:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        except sqlite3.Error as exc:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise DeliverySubscriptionError(
                "delivery_subscription_update_failed"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
