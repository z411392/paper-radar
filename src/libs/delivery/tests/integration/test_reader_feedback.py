import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.delivery.adapters.driven.sqlite_delivery_store_adapter import (
    SqliteDeliveryStoreAdapter,
)
from libs.delivery.application.commands.record_feedback import RecordFeedback
from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)


NOW = datetime(2026, 9, 26, 6, 20, tzinfo=timezone.utc)
WORK = "work:feedback"
PROFILE = "profile:feedback"
READER = "reader:local"


def _schema(root: Path) -> SqliteSchemaConnectionFactory:
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    assert info.schema_version == 23
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=23)
    connection = schema.connect()
    try:
        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "Feedback paper",
                "published",
                "2026-09-26",
                "day",
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO watch_profiles VALUES(?,?,?,?,?,?)",
            (
                PROFILE,
                READER,
                "Feedback profile",
                "active",
                None,
                NOW.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return schema


def _command(schema: SqliteSchemaConnectionFactory) -> RecordFeedback:
    return RecordFeedback(SqliteDeliveryStoreAdapter(schema.connect))


def _record(
    command: RecordFeedback,
    feedback_id: str,
    action: str,
    *,
    reader_id: str = READER,
    work_id: str = WORK,
    profile_id: str | None = PROFILE,
    created_at: datetime = NOW,
):
    return command(
        feedback_id=feedback_id,
        reader_id=reader_id,
        work_id=work_id,
        action=action,
        profile_id=profile_id,
        created_at=created_at,
    )


def _feedback_rows(schema: SqliteSchemaConnectionFactory) -> list[tuple[object, ...]]:
    connection = schema.connect()
    try:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT id,reader_id,work_id,action,profile_id,created_at "
                "FROM reader_feedback ORDER BY rowid"
            ).fetchall()
        ]
    finally:
        connection.close()


def _paper_state(schema: SqliteSchemaConnectionFactory) -> dict[str, list[tuple[object, ...]]]:
    connection = schema.connect()
    try:
        state: dict[str, list[tuple[object, ...]]] = {}
        for table in (
            "paper_works",
            "paper_manifestations",
            "paper_revisions",
            "research_events",
            "paper_claims",
            "relevance_assessments",
        ):
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            state[table] = [tuple(row) for row in rows]
        return state
    finally:
        connection.close()


def test_same_feedback_id_exact_replay_is_read_only(tmp_path: Path) -> None:
    schema = _schema(tmp_path)
    command = _command(schema)

    first = _record(command, "feedback:one", "useful")
    second = _record(command, "feedback:one", "useful")

    assert first.feedback_id == "feedback:one"
    assert first.replayed is False
    assert second.feedback_id == "feedback:one"
    assert second.replayed is True
    assert len(_feedback_rows(schema)) == 1


def test_same_feedback_id_with_different_payload_conflicts(tmp_path: Path) -> None:
    schema = _schema(tmp_path)
    command = _command(schema)
    _record(command, "feedback:one", "useful")

    with pytest.raises(DeliveryStoreError, match="reader_feedback_conflict"):
        _record(command, "feedback:one", "irrelevant")

    assert [row[3] for row in _feedback_rows(schema)] == ["useful"]


def test_saved_unsaved_saved_are_three_append_only_events(tmp_path: Path) -> None:
    schema = _schema(tmp_path)
    command = _command(schema)

    _record(command, "feedback:save-1", "saved")
    _record(
        command,
        "feedback:unsave-1",
        "unsaved",
        created_at=NOW + timedelta(seconds=1),
    )
    _record(
        command,
        "feedback:save-2",
        "saved",
        created_at=NOW + timedelta(seconds=2),
    )

    assert [(row[0], row[3]) for row in _feedback_rows(schema)] == [
        ("feedback:save-1", "saved"),
        ("feedback:unsave-1", "unsaved"),
        ("feedback:save-2", "saved"),
    ]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("feedback_id", "", "invalid_feedback_id"),
        ("reader_id", " ", "invalid_feedback_reader_id"),
        ("work_id", "\x00bad", "invalid_feedback_work_id"),
        ("action", "liked", "invalid_feedback_action"),
    ],
)
def test_invalid_feedback_fields_fail_closed(
    tmp_path: Path,
    field: str,
    value: str,
    error: str,
) -> None:
    schema = _schema(tmp_path)
    command = _command(schema)
    values = {
        "feedback_id": "feedback:bad",
        "reader_id": READER,
        "work_id": WORK,
        "action": "read",
    }
    values[field] = value

    with pytest.raises(DeliveryStoreError, match=error):
        command(
            **values,
            profile_id=PROFILE,
            created_at=NOW,
        )

    assert _feedback_rows(schema) == []


def test_naive_feedback_time_is_rejected(tmp_path: Path) -> None:
    schema = _schema(tmp_path)
    command = _command(schema)

    with pytest.raises(DeliveryStoreError, match="invalid_feedback_created_at"):
        _record(
            command,
            "feedback:bad-time",
            "read",
            created_at=datetime(2026, 9, 26, 6, 20),
        )

    assert _feedback_rows(schema) == []


def test_missing_work_and_profile_are_owner_errors(tmp_path: Path) -> None:
    schema = _schema(tmp_path)
    command = _command(schema)

    with pytest.raises(DeliveryStoreError, match="reader_feedback_work_missing"):
        _record(command, "feedback:missing-work", "read", work_id="work:missing")

    with pytest.raises(DeliveryStoreError, match="reader_feedback_profile_missing"):
        _record(
            command,
            "feedback:missing-profile",
            "read",
            profile_id="profile:missing",
        )

    assert _feedback_rows(schema) == []


def test_feedback_does_not_mutate_research_facts_or_relevance(tmp_path: Path) -> None:
    schema = _schema(tmp_path)
    before = _paper_state(schema)

    _record(_command(schema), "feedback:private", "irrelevant")

    assert _paper_state(schema) == before
    assert [row[3] for row in _feedback_rows(schema)] == ["irrelevant"]


def test_reopen_preserves_feedback_and_replay_identity(tmp_path: Path) -> None:
    schema = _schema(tmp_path)

    first = _record(_command(schema), "feedback:reopen", "read", profile_id=None)
    second = _record(_command(schema), "feedback:reopen", "read", profile_id=None)

    assert first.replayed is False
    assert second.replayed is True
    assert _feedback_rows(schema) == [
        (
            "feedback:reopen",
            READER,
            WORK,
            "read",
            None,
            NOW.isoformat(),
        )
    ]


def test_profile_is_optional_for_private_feedback(tmp_path: Path) -> None:
    schema = _schema(tmp_path)

    result = _record(
        _command(schema),
        "feedback:no-profile",
        "relevant_not_urgent",
        profile_id=None,
    )

    assert result.replayed is False
    assert _feedback_rows(schema)[0][4] is None


def _install_forbidden_write_guards(
    schema: SqliteSchemaConnectionFactory,
) -> None:
    guarded_tables = (
        "paper_works",
        "paper_manifestations",
        "paper_revisions",
        "research_events",
        "paper_claims",
        "relevance_assessments",
        "model_runs",
        "summary_revisions",
        "current_summaries",
    )
    connection = schema.connect()
    try:
        for table in guarded_tables:
            for verb in ("INSERT", "UPDATE", "DELETE"):
                trigger = f"guard_feedback_{table}_{verb.lower()}"
                connection.execute(
                    f"CREATE TRIGGER {trigger} BEFORE {verb} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'feedback_forbidden_write'); END"
                )
        connection.commit()
    finally:
        connection.close()


def test_feedback_write_path_is_confined_to_reader_feedback(
    tmp_path: Path,
) -> None:
    schema = _schema(tmp_path)
    _install_forbidden_write_guards(schema)

    result = _record(
        _command(schema),
        "feedback:isolated",
        "useful",
        profile_id=None,
    )

    assert result.replayed is False
    assert [(row[0], row[3]) for row in _feedback_rows(schema)] == [
        ("feedback:isolated", "useful")
    ]

