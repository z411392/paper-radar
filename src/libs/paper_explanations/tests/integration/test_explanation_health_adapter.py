import sqlite3
from pathlib import Path

import pytest

from libs.paper_explanations.adapters.driven.sqlite_explanation_health_adapter import (
    SqliteExplanationHealthAdapter,
)
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
)


ROOT = Path(__file__).resolve().parents[5]


def setup_database(tmp_path: Path) -> Path:
    database = tmp_path / "health.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.executescript(
        (ROOT / "migrations/0005-paper-explanations.sql").read_text(encoding="utf-8")
    )
    connection.execute(
        "INSERT INTO model_runs("
        "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,state,started_at"
        ") VALUES('run:reserved','summary','gateway','model','p','i','reserved','2026-09-26')"
    )
    connection.execute(
        "INSERT INTO model_runs("
        "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,state,started_at"
        ") VALUES('run:settled','summary','gateway','model','p2','i2','succeeded','2026-09-26')"
    )
    connection.execute(
        "INSERT INTO model_runs("
        "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,state,started_at"
        ") VALUES('run:unknown','summary','gateway','model','p3','i3','failed','2026-09-26')"
    )
    connection.execute(
        "INSERT INTO usage_reservations VALUES("
        "'usage:reserved','run:reserved','2026-09','USD',100,NULL,'reserved','2026-09-26')"
    )
    connection.execute(
        "INSERT INTO usage_reservations VALUES("
        "'usage:settled','run:settled','2026-09','USD',600,533,'settled','2026-09-26')"
    )
    connection.execute(
        "INSERT INTO usage_reservations VALUES("
        "'usage:unknown','run:unknown','2026-09','USD',500,NULL,'unknown','2026-09-26')"
    )
    connection.execute(
        "INSERT INTO summary_revisions VALUES("
        "'summary:rejected','revision:1','work:1','snapshot:1','g','run:settled',"
        "'object:1','zh-TW','plain','rejected','2026-09-26')"
    )
    connection.commit()
    connection.close()
    return database


def factory(database: Path):
    def connect() -> sqlite3.Connection:
        return sqlite3.connect(database, isolation_level=None)

    return connect


def test_reads_reserved_exposure_settled_actual_unknown_cost_and_qa_reject(tmp_path: Path):
    health = SqliteExplanationHealthAdapter(factory(setup_database(tmp_path)))()

    assert health.qa_rejected == 1
    assert len(health.usage_periods) == 1
    period = health.usage_periods[0]
    assert period.period_key == "2026-09"
    assert period.currency == "USD"
    assert period.reserved_micros == 600
    assert period.settled_actual_micros == 533
    assert period.unknown_cost_reservations == 1


def test_period_and_currency_boundaries_are_preserved(tmp_path: Path):
    database = setup_database(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO model_runs("
        "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,state,started_at"
        ") VALUES('run:twd','summary','gateway','model','p4','i4','reserved','2026-09-26')"
    )
    connection.execute(
        "INSERT INTO usage_reservations VALUES("
        "'usage:twd','run:twd','2026-10','TWD',30,NULL,'reserved','2026-09-26')"
    )
    connection.commit()
    connection.close()

    health = SqliteExplanationHealthAdapter(factory(database))()

    assert [(item.period_key, item.currency) for item in health.usage_periods] == [
        ("2026-09", "USD"),
        ("2026-10", "TWD"),
    ]
    assert health.usage_periods[1].reserved_micros == 30
