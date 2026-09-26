import sqlite3
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_delivery_health_adapter import (
    SqliteDeliveryHealthAdapter,
)
from libs.delivery.exceptions.delivery_health_error import DeliveryHealthError


ROOT = Path(__file__).resolve().parents[5]


def setup_database(tmp_path: Path) -> Path:
    database = tmp_path / "health.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.executescript(
        (ROOT / "migrations/0007-delivery.sql").read_text(encoding="utf-8")
    )
    connection.execute(
        "INSERT INTO digests VALUES("
        "'digest:1','subscription:1','2026-09-26','2026-09-26T08:00:00+00:00',"
        "NULL,'unknown','2026-09-26T08:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES("
        "'outbox:unknown','digest:1','request:1','a','unknown',1,NULL,"
        "'2026-09-26T08:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO delivery_attempts VALUES("
        "'attempt:unknown','outbox:unknown',1,'unknown',NULL,'timeout',"
        "'2026-09-26T08:01:00+00:00','2026-09-26T08:02:00+00:00')"
    )
    connection.execute(
        "INSERT INTO notification_ledger VALUES("
        "'notification:1','reader:1','event:1','email','outbox:unknown','unknown',"
        "'2026-09-26T08:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO delivery_outbox VALUES("
        "'outbox:pending','digest:2','request:2','b','pending',1,NULL,"
        "'2026-09-26T08:00:00+00:00')"
    )
    connection.commit()
    connection.close()
    return database


def factory(database: Path):
    def connect() -> sqlite3.Connection:
        return sqlite3.connect(database, isolation_level=None)

    return connect


def test_unknown_delivery_is_reconciled_from_durable_delivery_ledgers(tmp_path: Path):
    health = SqliteDeliveryHealthAdapter(factory(setup_database(tmp_path)))()

    assert health.unknown_deliveries == 1


def test_unknown_outbox_with_non_unknown_attempt_fails_closed(tmp_path: Path):
    database = setup_database(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE delivery_attempts SET state='failed' WHERE id='attempt:unknown'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(DeliveryHealthError, match="delivery_health_ledger_mismatch"):
        SqliteDeliveryHealthAdapter(factory(database))()
