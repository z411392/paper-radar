import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]


def _run(*args: str):
    return subprocess.run(
        [sys.executable, "-m", "apps.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _init(workspace: Path, *schema: str) -> None:
    result = _run(
        "init",
        "--workspace",
        str(workspace),
        *schema,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _configure(workspace: Path, *extra: str):
    return _run(
        "delivery",
        "configure",
        "--workspace",
        str(workspace),
        "--reader-id",
        "reader:local",
        "--timezone",
        "Asia/Taipei",
        "--local-time",
        "08:00",
        "--max-items",
        "5",
        "--recipient-ref",
        "recipient:primary",
        "--enabled",
        *extra,
    )


def test_delivery_configure_show_and_replay_on_clean_runtime(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime"
    _init(workspace, "--with-runtime")

    first = _configure(workspace)
    replay = _configure(workspace)
    shown = _run(
        "delivery",
        "show",
        "--workspace",
        str(workspace),
        "--reader-id",
        "reader:local",
    )

    assert first.returncode == replay.returncode == shown.returncode == 0
    left = json.loads(first.stdout)["subscription"]
    again = json.loads(replay.stdout)["subscription"]
    current = json.loads(shown.stdout)["subscription"]
    assert left["policy_version"] == again["policy_version"] == 1
    assert left["replayed"] is False
    assert again["replayed"] is True
    assert current["replayed"] is False
    assert current["timezone"] == "Asia/Taipei"
    assert current["local_time"] == "08:00"
    assert current["recipient_ref"] == "recipient:primary"

    connection = sqlite3.connect(workspace / "state" / "app.sqlite3")
    try:
        effects = connection.execute(
            "SELECT external_effects_enabled FROM workspace_metadata "
            "WHERE singleton=1"
        ).fetchone()[0]
        count = connection.execute(
            "SELECT count(*) FROM delivery_subscriptions"
        ).fetchone()[0]
    finally:
        connection.close()
    assert effects == 0
    assert count == 1


def test_delivery_show_missing_is_normal_null_result(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    _init(workspace, "--with-runtime")

    shown = _run(
        "delivery",
        "show",
        "--workspace",
        str(workspace),
        "--reader-id",
        "reader:local",
    )

    assert shown.returncode == 0
    assert json.loads(shown.stdout) == {"subscription": None}


def test_delivery_disable_and_reenable_only_change_subscription_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime"
    _init(workspace, "--with-runtime")
    assert _configure(workspace).returncode == 0

    disabled = _run(
        "delivery",
        "configure",
        "--workspace",
        str(workspace),
        "--reader-id",
        "reader:local",
        "--timezone",
        "Asia/Taipei",
        "--local-time",
        "08:00",
        "--max-items",
        "5",
        "--recipient-ref",
        "recipient:primary",
        "--disabled",
    )
    enabled = _configure(workspace)

    assert disabled.returncode == enabled.returncode == 0
    assert json.loads(disabled.stdout)["subscription"]["policy_version"] == 2
    assert json.loads(enabled.stdout)["subscription"]["policy_version"] == 3

    connection = sqlite3.connect(workspace / "state" / "app.sqlite3")
    try:
        assert connection.execute(
            "SELECT count(*) FROM delivery_outbox"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM notification_ledger"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_delivery_invalid_timezone_has_zero_mutation(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    _init(workspace, "--with-runtime")

    result = _run(
        "delivery",
        "configure",
        "--workspace",
        str(workspace),
        "--reader-id",
        "reader:local",
        "--timezone",
        "Mars/Olympus",
        "--local-time",
        "08:00",
        "--max-items",
        "5",
        "--recipient-ref",
        "recipient:primary",
        "--enabled",
    )

    assert result.returncode == 1
    assert json.loads(result.stderr)["error"]["code"] == (
        "invalid_delivery_timezone"
    )
    connection = sqlite3.connect(workspace / "state" / "app.sqlite3")
    try:
        assert connection.execute(
            "SELECT count(*) FROM delivery_subscriptions"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_delivery_discovery_workspace_requires_explicit_upgrade(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "discovery"
    _init(workspace, "--with-discovery")

    result = _configure(workspace)

    assert result.returncode == 1
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "schema_upgrade_required"
    assert "--with-runtime" in error["hint"]


def test_delivery_missing_workspace_is_never_created(tmp_path: Path) -> None:
    workspace = tmp_path / "missing"

    result = _configure(workspace)

    assert result.returncode == 1
    assert json.loads(result.stderr)["error"]["code"] == "workspace_missing"
    assert not workspace.exists()
