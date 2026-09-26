import sqlite3
from collections.abc import Callable

from libs.research_workflow.dtos.operational_health import DeliveryHealthEvidence
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
)


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
            raise OperationalHealthError("delivery_health_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        if type(unknown) is not int or unknown < 0:
            raise OperationalHealthError("delivery_health_corrupt")
        return DeliveryHealthEvidence(unknown_deliveries=unknown)
