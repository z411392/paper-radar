import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime

from libs.delivery.domain.services.delivery_subscription_rules import (
    DeliverySubscriptionRules as Rules,
)
from libs.delivery.dtos.delivery_subscription import (
    ConfigureDeliverySubscriptionRequest,
    DeliverySubscription,
)
from libs.delivery.exceptions.delivery_subscription_error import (
    DeliverySubscriptionError,
)


class SqliteDeliverySubscriptionStoreAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            if connection.in_transaction:
                raise DeliverySubscriptionError("owned_connection_required")
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise DeliverySubscriptionError("foreign_keys_required")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        except DeliverySubscriptionError:
            raise
        except sqlite3.Error as exc:
            primary = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            code = (
                "delivery_subscription_busy"
                if primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "delivery_subscription_database_error"
            )
            raise DeliverySubscriptionError(code) from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _decode(row: sqlite3.Row, *, replayed: bool) -> DeliverySubscription:
        try:
            schedule = json.loads(row["schedule_json"])
            if (
                not isinstance(schedule, dict)
                or set(schedule) != {"kind", "local_time"}
                or schedule["kind"] != "daily"
                or not isinstance(schedule["local_time"], str)
            ):
                raise ValueError("schedule")
            request = ConfigureDeliverySubscriptionRequest(
                reader_id=row["reader_id"],
                timezone=row["timezone"],
                local_time=schedule["local_time"],
                max_items=row["max_items"],
                recipient_ref=row["recipient_ref"],
                enabled=bool(row["enabled"]),
            )
            normalized = Rules.normalize(request)
            if (
                row["schedule_json"] != normalized[2]
                or row["channel"] != "email"
                or type(row["enabled"]) is not int
                or row["enabled"] not in {0, 1}
                or type(row["policy_version"]) is not int
                or not 1 <= row["policy_version"] < 2**63
            ):
                raise ValueError("state")
            created = datetime.fromisoformat(row["created_at"])
            created = Rules.instant(created)
        except (
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
            DeliverySubscriptionError,
        ):
            raise DeliverySubscriptionError(
                "delivery_subscription_state_corrupt"
            ) from None
        return DeliverySubscription(
            row["id"],
            normalized[0],
            "email",
            normalized[5],
            normalized[1],
            schedule["local_time"],
            normalized[3],
            normalized[4],
            row["policy_version"],
            created,
            replayed,
        )

    def configure(
        self,
        request: ConfigureDeliverySubscriptionRequest,
        *,
        now: datetime,
    ) -> DeliverySubscription:
        desired = Rules.normalize(request)
        created = Rules.instant(now).isoformat()
        reader_id, tz, schedule, max_items, recipient_ref, enabled = desired
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM delivery_subscriptions "
                "WHERE reader_id=? AND channel='email'",
                (reader_id,),
            ).fetchone()
            if row is None:
                subscription_id = Rules.subscription_id(reader_id)
                connection.execute(
                    "INSERT INTO delivery_subscriptions("
                    "id,reader_id,channel,enabled,timezone,schedule_json,"
                    "max_items,recipient_ref,policy_version,created_at"
                    ") VALUES(?,?,'email',?,?,?,?,?,1,?)",
                    (
                        subscription_id,
                        reader_id,
                        1 if enabled else 0,
                        tz,
                        schedule,
                        max_items,
                        recipient_ref,
                        created,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM delivery_subscriptions WHERE id=?",
                    (subscription_id,),
                ).fetchone()
                assert row is not None
                return self._decode(row, replayed=False)

            current = self._decode(row, replayed=False)
            current_tuple = (
                current.reader_id,
                current.timezone,
                json.dumps(
                    {"kind": "daily", "local_time": current.local_time},
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                current.max_items,
                current.recipient_ref,
                current.enabled,
            )
            if current_tuple == desired:
                return self._decode(row, replayed=True)
            if current.policy_version >= 2**63 - 1:
                raise DeliverySubscriptionError(
                    "delivery_subscription_revision_exhausted"
                )
            changed = connection.execute(
                "UPDATE delivery_subscriptions SET enabled=?,timezone=?,"
                "schedule_json=?,max_items=?,recipient_ref=?,"
                "policy_version=policy_version+1 "
                "WHERE id=? AND policy_version=?",
                (
                    1 if enabled else 0,
                    tz,
                    schedule,
                    max_items,
                    recipient_ref,
                    current.subscription_id,
                    current.policy_version,
                ),
            ).rowcount
            if changed != 1:
                raise DeliverySubscriptionError(
                    "delivery_subscription_concurrent_update"
                )
            updated = connection.execute(
                "SELECT * FROM delivery_subscriptions WHERE id=?",
                (current.subscription_id,),
            ).fetchone()
            assert updated is not None
            return self._decode(updated, replayed=False)

    def read(self, reader_id: str) -> DeliverySubscription | None:
        normalized = Rules.reader_id(reader_id)
        with self._transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM delivery_subscriptions "
                "WHERE reader_id=? AND channel='email'",
                (normalized,),
            ).fetchone()
            return None if row is None else self._decode(row, replayed=False)
