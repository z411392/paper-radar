import sqlite3
from collections.abc import Callable

from libs.delivery.dtos.delivery_health import DeliveryHealthEvidence
from libs.delivery.exceptions.delivery_health_error import DeliveryHealthError


class SqliteDeliveryHealthAdapter:
    def __init__(self, connect: Callable[[], sqlite3.Connection]) -> None:
        self._connect = connect

    @staticmethod
    def _unknown_rows(connection: sqlite3.Connection) -> tuple[sqlite3.Row, ...]:
        return tuple(
            connection.execute(
                "SELECT o.id,d.state AS digest_state,"
                "(SELECT a.state FROM delivery_attempts a "
                "WHERE a.outbox_id=o.id ORDER BY a.attempt_no DESC LIMIT 1) "
                "AS latest_attempt_state,"
                "(SELECT COUNT(*) FROM notification_ledger n "
                "WHERE n.outbox_id=o.id) AS ledger_count,"
                "(SELECT COUNT(*) FROM notification_ledger n "
                "WHERE n.outbox_id=o.id AND n.state<>'unknown') AS non_unknown_ledgers "
                "FROM delivery_outbox o "
                "LEFT JOIN digests d ON d.id=o.digest_id "
                "WHERE o.state='unknown' ORDER BY o.id"
            ).fetchall()
        )

    @staticmethod
    def _reconcile_unknown(rows: tuple[sqlite3.Row, ...]) -> int:
        for row in rows:
            if (
                not isinstance(row["id"], str)
                or not row["id"]
                or row["digest_state"] != "unknown"
                or row["latest_attempt_state"] != "unknown"
                or type(row["ledger_count"]) is not int
                or row["ledger_count"] < 1
                or type(row["non_unknown_ledgers"]) is not int
                or row["non_unknown_ledgers"] != 0
            ):
                raise DeliveryHealthError("delivery_health_ledger_mismatch")
        return len(rows)

    def __call__(self) -> DeliveryHealthEvidence:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            rows = self._unknown_rows(connection)
            connection.commit()
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            raise DeliveryHealthError("delivery_health_database_error") from exc
        finally:
            if connection is not None:
                connection.close()

        return DeliveryHealthEvidence(
            unknown_deliveries=self._reconcile_unknown(rows)
        )
