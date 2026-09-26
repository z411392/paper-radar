import sqlite3
from pathlib import Path

from libs.delivery.adapters.driven.sqlite_delivery_health_adapter import (
    SqliteDeliveryHealthAdapter,
)


ROOT = Path(__file__).resolve().parents[5]


def test_unknown_delivery_is_read_from_durable_outbox_state(tmp_path: Path):
    database = tmp_path / "health.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.executescript(
        (ROOT / "migrations/0007-delivery.sql").read_text(encoding="utf-8")
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES("
        "'outbox:unknown','digest:1','request:1','a','unknown',1,NULL,'2026-09-26')"
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES("
        "'outbox:pending','digest:2','request:2','b','pending',1,NULL,'2026-09-26')"
    )
    connection.commit()
    connection.close()

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(database, isolation_level=None)

    health = SqliteDeliveryHealthAdapter(connect)()

    assert health.unknown_deliveries == 1
