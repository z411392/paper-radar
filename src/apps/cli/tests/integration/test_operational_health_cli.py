import json

import pytest

from apps.cli.adapters.driving.inspect_health import run_health_cli
from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)


def test_health_cli_replays_empty_workspace_without_enabling_effects(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "runtime"
    info = SqliteWorkspaceBootstrapAdapter(
        workspace,
        load_workspace_migrations(with_runtime=True),
    ).initialize()
    assert info.schema_version == 25
    assert info.external_effects_enabled is False

    run_health_cli(["health", "--workspace", str(workspace)])
    first = json.loads(capsys.readouterr().out)
    run_health_cli(["health", "--workspace", str(workspace)])
    second = json.loads(capsys.readouterr().out)

    assert first == second
    assert first["sources"] == []
    assert first["coverage_gaps"] == []
    assert first["explanations"] == {"qa_rejected": 0, "usage_periods": []}
    assert first["delivery"] == {"unknown_deliveries": 0}
    assert first["runtime"]["sqlite_version"]
    assert first["runtime"]["sqlite_source_id"]

    connection = SqliteConnectionFactory(workspace).connect()
    try:
        assert connection.execute(
            "SELECT external_effects_enabled FROM workspace_metadata WHERE singleton=1"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_health_cli_rejects_previous_runtime_schema_as_structured_error(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    info = SqliteWorkspaceBootstrapAdapter(workspace, migrations[:24]).initialize()
    assert info.schema_version == 24

    with pytest.raises(SystemExit) as raised:
        run_health_cli(["health", "--workspace", str(workspace)])

    assert raised.value.code == 1
    output = json.loads(capsys.readouterr().err)
    assert output == {"error": {"code": "schema_upgrade_required"}}


def test_health_cli_reports_cost_ledger_mismatch_as_structured_error(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        workspace,
        load_workspace_migrations(with_runtime=True),
    ).initialize()

    connection = SqliteConnectionFactory(workspace).connect()
    try:
        connection.execute(
            "INSERT INTO model_runs("
            "id,task_kind,provider,model_name,prompt_digest,input_fingerprint,state,"
            "actual_cost_micros,started_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "run:health-mismatch",
                "summary",
                "gateway",
                "model",
                "prompt",
                "input",
                "succeeded",
                534,
                "2026-09-26T08:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO usage_reservations("
            "id,run_id,period_key,currency,reserved_micros,actual_micros,state,created_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            (
                "usage:health-mismatch",
                "run:health-mismatch",
                "2026-09",
                "USD",
                600,
                533,
                "settled",
                "2026-09-26T08:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SystemExit) as raised:
        run_health_cli(["health", "--workspace", str(workspace)])

    assert raised.value.code == 1
    output = json.loads(capsys.readouterr().err)
    assert output == {"error": {"code": "explanation_health_ledger_mismatch"}}


def test_health_cli_reports_delivery_ledger_mismatch_as_structured_error(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        workspace,
        load_workspace_migrations(with_runtime=True),
    ).initialize()

    connection = SqliteConnectionFactory(workspace).connect()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO digests VALUES("
            "'digest:health','subscription:health','2026-09-26',"
            "'2026-09-26T08:00:00+00:00',NULL,'unknown',"
            "'2026-09-26T08:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO delivery_outbox VALUES("
            "'outbox:health','digest:health','request:health','a','unknown',1,NULL,"
            "'2026-09-26T08:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO delivery_attempts VALUES("
            "'attempt:health','outbox:health',1,'failed',NULL,'timeout',"
            "'2026-09-26T08:01:00+00:00','2026-09-26T08:02:00+00:00')"
        )
        connection.execute(
            "INSERT INTO notification_ledger VALUES("
            "'notification:health','reader:health','event:health','email',"
            "'outbox:health','unknown','2026-09-26T08:00:00+00:00')"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SystemExit) as raised:
        run_health_cli(["health", "--workspace", str(workspace)])

    assert raised.value.code == 1
    output = json.loads(capsys.readouterr().err)
    assert output == {"error": {"code": "delivery_health_ledger_mismatch"}}


def test_health_cli_reports_workflow_ledger_mismatch_as_structured_error(
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "runtime"
    SqliteWorkspaceBootstrapAdapter(
        workspace,
        load_workspace_migrations(with_runtime=True),
    ).initialize()

    connection = SqliteConnectionFactory(workspace).connect()
    try:
        input_json = json.dumps(
            {
                "binding_key": "personal:3:statistics:7:arxiv",
                "profile_id": "personal",
                "profile_revision": 3,
                "domain_id": "statistics",
                "domain_revision": 7,
                "source_id": "arxiv",
                "window_start": "2026-09-25T00:00:00+00:00",
                "window_end": "2026-09-26T00:00:00+00:00",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO workflow_jobs("
            "id,job_kind,business_key,input_json,input_fingerprint,state,due_at,"
            "fencing_token,attempt_count,created_at"
            ") VALUES(?,?,?,?,?,'succeeded',?,1,1,?)",
            (
                "job:health-mismatch",
                "harvest_window",
                "harvest:health-mismatch",
                input_json,
                "a" * 64,
                "2026-09-26T00:00:00+00:00",
                "2026-09-25T23:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO job_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "attempt:health-mismatch",
                "job:health-mismatch",
                1,
                1,
                "failed",
                "source_unavailable",
                "2026-09-25T23:01:00+00:00",
                "2026-09-25T23:02:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SystemExit) as raised:
        run_health_cli(["health", "--workspace", str(workspace)])

    assert raised.value.code == 1
    output = json.loads(capsys.readouterr().err)
    assert output == {"error": {"code": "workflow_health_ledger_mismatch"}}
