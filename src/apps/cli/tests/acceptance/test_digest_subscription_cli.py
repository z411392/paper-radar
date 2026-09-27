import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def ok(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_subscribe_email_is_idempotent_and_updates_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime"
    initialized = ok(
        cli(
            tmp_path,
            "init",
            "--workspace",
            str(workspace),
            "--with-runtime",
        )
    )
    assert initialized["schema_version"] == 24

    args = (
        "digest",
        "subscribe-email",
        "--workspace",
        str(workspace),
        "--reader-id",
        "local",
        "--recipient-ref",
        "recipient:primary",
        "--timezone",
        "UTC",
        "--local-time",
        "08:00",
    )
    first = ok(cli(tmp_path, *args))
    second = ok(cli(tmp_path, *args))

    assert first == second == {
        "channel": "email",
        "enabled": True,
        "id": "subscription:email:local",
        "local_time": "08:00",
        "max_items": 5,
        "policy_version": 1,
        "reader_id": "local",
        "recipient_ref": "recipient:primary",
        "timezone": "UTC",
    }

    changed = ok(
        cli(
            tmp_path,
            *args[:-1],
            "09:00",
            "--max-items",
            "3",
        )
    )
    assert changed["id"] == "subscription:email:local"
    assert changed["local_time"] == "09:00"
    assert changed["max_items"] == 3
    assert changed["policy_version"] == 2

    with sqlite3.connect(workspace / "state/app.sqlite3") as connection:
        row = connection.execute(
            "SELECT id,reader_id,channel,enabled,timezone,schedule_json,"
            "max_items,recipient_ref,policy_version "
            "FROM delivery_subscriptions"
        ).fetchone()

    assert row == (
        "subscription:email:local",
        "local",
        "email",
        1,
        "UTC",
        '{"kind":"daily","local_time":"09:00"}',
        3,
        "recipient:primary",
        2,
    )


def test_subscribe_email_requires_runtime_schema(tmp_path: Path) -> None:
    workspace = tmp_path / "profiles-only"
    initialized = ok(
        cli(
            tmp_path,
            "init",
            "--workspace",
            str(workspace),
            "--with-profiles",
        )
    )
    assert initialized["schema_version"] == 2

    result = cli(
        tmp_path,
        "digest",
        "subscribe-email",
        "--workspace",
        str(workspace),
        "--reader-id",
        "local",
        "--recipient-ref",
        "recipient:primary",
        "--timezone",
        "UTC",
        "--local-time",
        "08:00",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "schema_upgrade_required"
    assert "--with-runtime" in error["hint"]
