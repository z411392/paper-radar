import sqlite3

from libs.research_workflow.adapters.driven.sqlite_runtime_health_adapter import (
    SqliteRuntimeHealthAdapter,
)


def test_runtime_health_reads_engine_identity_from_the_actual_connection():
    def connect() -> sqlite3.Connection:
        return sqlite3.connect(":memory:")

    health = SqliteRuntimeHealthAdapter(connect)()

    assert health.python_implementation
    assert health.python_version
    assert health.sqlite_version == sqlite3.sqlite_version
    assert health.sqlite_source_id
    assert health.sqlite_source_id == sqlite3.connect(":memory:").execute(
        "SELECT sqlite_source_id()"
    ).fetchone()[0]
