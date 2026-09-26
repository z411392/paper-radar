import sqlite3
from collections.abc import Callable

from libs.delivery.dtos.delivery_health import DeliveryHealthEvidence
from libs.delivery.exceptions.delivery_health_error import DeliveryHealthError


class SqliteDeliveryHealthAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    def __call__(self) -> DeliveryHealthEvidence:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            unknown = connection.execute(
                "SELECT COUNT(*) FROM delivery_outbox WHERE state='unknown'"
            ).fetchone()[0]
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise DeliveryHealthError("delivery_health_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        if type(unknown) is not int or unknown < 0:
            raise DeliveryHealthError("delivery_health_corrupt")
        return DeliveryHealthEvidence(unknown_deliveries=unknown)
