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


def test_digest_status_from_worker_env_shows_sent_and_pending_items(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime-status"
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

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        f"PAPER_RADAR_WORKSPACE={workspace}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    with sqlite3.connect(workspace / "state/app.sqlite3") as connection:
        connection.execute(
            "INSERT INTO delivery_subscriptions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "subscription:email:local",
                "local",
                "email",
                1,
                "Asia/Taipei",
                '{"kind":"daily","local_time":"08:00"}',
                5,
                "recipient:primary",
                1,
                "2026-09-28T00:00:00+00:00",
            ),
        )
        for work_id, title in (
            ("work:sent", "Already sent paper"),
            ("work:pending", "Pending paper"),
        ):
            connection.execute(
                "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
                (
                    work_id,
                    title,
                    "preprint",
                    None,
                    None,
                    "2026-09-28T00:00:00+00:00",
                    "2026-09-28T00:00:00+00:00",
                ),
            )
        for event_id, work_id in (
            ("event:sent", "work:sent"),
            ("event:pending", "work:pending"),
        ):
            connection.execute(
                "INSERT INTO research_events VALUES(?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    work_id,
                    None,
                    "new_work",
                    "key:" + event_id,
                    "{}",
                    "2026-09-28T00:00:00+00:00",
                    "2026-09-28T00:00:00+00:00",
                ),
            )

        connection.execute(
            "INSERT INTO digests VALUES(?,?,?,?,?,?,?)",
            (
                "digest:sent",
                "subscription:email:local",
                "2026-09-28",
                "2026-09-28T00:00:00+00:00",
                None,
                "sent",
                "2026-09-28T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO delivery_outbox VALUES(?,?,?,?,?,?,?,?)",
            (
                "outbox:sent",
                "digest:sent",
                "delivery:sent",
                "a" * 64,
                "provider_accepted",
                1,
                None,
                "2026-09-28T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO digest_items VALUES(?,?,?,?,?,?,?)",
            (
                "digest:sent",
                1,
                "event:sent",
                "work:sent",
                None,
                None,
                "status_notice",
            ),
        )
        connection.execute(
            "UPDATE digest_items SET item_kind='paper',"
            "summary_id='summary:sent',revision_id='revision:sent' "
            "WHERE digest_id='digest:sent'"
        )
        connection.execute(
            "INSERT INTO notification_ledger VALUES(?,?,?,?,?,?,?)",
            (
                "notification:sent",
                "local",
                "event:sent",
                "email",
                "outbox:sent",
                "accepted",
                "2026-09-28T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO delivery_attempts VALUES(?,?,?,?,?,?,?,?)",
            (
                "attempt:sent",
                "outbox:sent",
                1,
                "provider_accepted",
                "provider:sent",
                None,
                "2026-09-28T00:00:01+00:00",
                "2026-09-28T00:00:02+00:00",
            ),
        )

        connection.execute(
            "INSERT INTO digests VALUES(?,?,?,?,?,?,?)",
            (
                "digest:pending",
                "subscription:email:local",
                "2026-09-29",
                "2026-09-29T00:00:00+00:00",
                None,
                "queued",
                "2026-09-29T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO delivery_outbox VALUES(?,?,?,?,?,?,?,?)",
            (
                "outbox:pending",
                "digest:pending",
                "delivery:pending",
                "b" * 64,
                "pending",
                1,
                None,
                "2026-09-29T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO digest_items VALUES(?,?,?,?,?,?,?)",
            (
                "digest:pending",
                1,
                "event:pending",
                "work:pending",
                "summary:pending",
                "revision:pending",
                "paper",
            ),
        )
        connection.execute(
            "INSERT INTO notification_ledger VALUES(?,?,?,?,?,?,?)",
            (
                "notification:pending",
                "local",
                "event:pending",
                "email",
                "outbox:pending",
                "reserved",
                "2026-09-29T00:00:00+00:00",
            ),
        )
        connection.commit()

    result = ok(
        cli(
            tmp_path,
            "digest",
            "status",
            "--env-file",
            str(env_file),
            "--limit",
            "10",
        )
    )

    assert result == {
        "deliveries": [
            {
                "attempt_count": 0,
                "delivery_state": "pending",
                "digest_id": "digest:pending",
                "items": [
                    {
                        "event_id": "event:pending",
                        "event_kind": "new_work",
                        "notification_state": "reserved",
                        "send_status": "pending",
                        "title": "Pending paper",
                        "work_id": "work:pending",
                    }
                ],
                "latest_error_code": None,
                "outbox_id": "outbox:pending",
                "period_key": "2026-09-29",
            },
            {
                "attempt_count": 1,
                "delivery_state": "sent",
                "digest_id": "digest:sent",
                "items": [
                    {
                        "event_id": "event:sent",
                        "event_kind": "new_work",
                        "notification_state": "accepted",
                        "send_status": "sent",
                        "title": "Already sent paper",
                        "work_id": "work:sent",
                    }
                ],
                "latest_error_code": None,
                "outbox_id": "outbox:sent",
                "period_key": "2026-09-28",
            },
        ]
    }
