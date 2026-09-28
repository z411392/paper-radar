import json
import subprocess
import sys
import xml.etree.ElementTree as ET
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


def test_runtime_workspace_can_run_one_idle_worker_cycle_and_restart(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    assert json.loads(initialized.stdout)["schema_version"] == 24

    first = _run("run-worker", "--workspace", str(workspace), "--once")
    second = _run("run-worker", "--workspace", str(workspace), "--once")

    assert first.returncode == second.returncode == 0
    left = json.loads(first.stdout)
    right = json.loads(second.stdout)
    assert left == right
    assert left["processed_jobs"] == 0
    assert left["jobs"] == []
    assert left["scheduler"]["new_jobs"] == 0
    assert left["scheduler"]["digest_deferred"] is False


def test_effects_enable_can_use_worker_env_file(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime-effects-env"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        f"PAPER_RADAR_WORKSPACE={workspace}\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    enabled = _run(
        "effects",
        "enable",
        "--env-file",
        str(env_file),
    )
    disabled = _run(
        "effects",
        "disable",
        "--env-file",
        str(env_file),
    )

    assert enabled.returncode == disabled.returncode == 0
    assert json.loads(enabled.stdout)["external_effects_enabled"] is True
    assert json.loads(disabled.stdout)["external_effects_enabled"] is False


def test_worker_never_auto_upgrades_a_discovery_only_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "discovery"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-discovery",
    )
    assert initialized.returncode == 0

    worker = _run("run-worker", "--workspace", str(workspace), "--once")

    assert worker.returncode == 1
    error = json.loads(worker.stderr)["error"]
    assert error["code"] == "schema_upgrade_required"
    assert "--with-runtime" in error["hint"]


def test_worker_stays_in_apps_cli_and_driving_handler_has_no_scheduler_business_logic() -> None:
    assert not (ROOT / "src/apps/worker").exists()
    source = (ROOT / "src/apps/cli/adapters/driving/run_worker.py").read_text(encoding="utf-8")
    assert "PlanCatchupJobs" not in source
    assert "sqlite3" not in source
    assert "RunWorkerCyclePort" in source


def test_launchd_template_uses_same_env_file_runtime_contract() -> None:
    path = ROOT / "deploy/macos/com.paper-radar.worker.plist.example"
    root = ET.parse(path).getroot()
    text = ET.tostring(root, encoding="unicode")

    assert "apps.cli" in text
    assert "run-worker" in text
    assert "--env-file" in text
    assert "__ENV_FILE__" in text
    assert "__PYTHON__" in text
    assert "__WORKSPACE__" not in text
    assert "--workspace" not in text
    assert "--poll-seconds" not in text
    assert "--with-runtime" not in text
    assert "--allow-live-source" not in text
    assert "--allow-live-mail" not in text
    assert "--recipient-email" not in text
    assert "--recipient-map-file" not in text
    assert "--smtp-password-file" not in text
    assert "--ncbi-email" not in text
    assert "--ncbi-api-key" not in text
    assert "--ncbi-rate-limit-state" not in text
    assert "--crossref-email" not in text
    assert "--crossref-rate-limit-dir" not in text
    assert "--allow-live-model" not in text
    assert "--openrouter-api-key-file" not in text
    assert "--model-period-limit-micros" not in text


def test_live_mail_single_recipient_commissioning_runs_idle_without_network(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime-mail"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0

    password_file = tmp_path / "smtp-password"
    password_file.write_text("fake-smtp-password\n", encoding="utf-8")
    password_file.chmod(0o600)

    worker = _run(
        "run-worker",
        "--workspace",
        str(workspace),
        "--once",
        "--allow-live-mail",
        "--recipient-email",
        "reader@example.com",
        "--smtp-host",
        "smtp.example.com",
        "--smtp-port",
        "465",
        "--smtp-sender",
        "paper-radar@example.com",
        "--smtp-username",
        "mailer@example.com",
        "--smtp-password-file",
        str(password_file),
    )

    assert worker.returncode == 0, worker.stdout + worker.stderr
    result = json.loads(worker.stdout)
    assert result["processed_jobs"] == 0
    assert result["jobs"] == []

def test_model_options_require_explicit_live_model_gate(tmp_path: Path) -> None:
    key_file = tmp_path / "openrouter.key"

    worker = _run(
        "run-worker",
        "--workspace",
        str(tmp_path / "runtime"),
        "--once",
        "--openrouter-api-key-file",
        str(key_file),
    )

    assert worker.returncode == 2
    assert "model options require --allow-live-model" in worker.stderr


def test_live_model_requires_complete_budget_commissioning(tmp_path: Path) -> None:
    worker = _run(
        "run-worker",
        "--workspace",
        str(tmp_path / "runtime"),
        "--once",
        "--allow-live-model",
    )

    assert worker.returncode == 2
    assert (
        "--allow-live-model requires one credential and complete model budget policy"
        in worker.stderr
    )


def test_complete_live_model_commissioning_runs_idle_without_network(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime-model"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0

    key_file = tmp_path / "openrouter.key"
    key_file.write_text(
        "sk-or-v1-fake-not-a-real-secret-000000\n",
        encoding="utf-8",
    )
    key_file.chmod(0o600)

    worker = _run(
        "run-worker",
        "--workspace",
        str(workspace),
        "--once",
        "--allow-live-model",
        "--openrouter-api-key-file",
        str(key_file),
        "--model-period-limit-micros",
        "1000000",
        "--model-reservation-micros",
        "10000",
    )

    assert worker.returncode == 0, worker.stdout + worker.stderr
    result = json.loads(worker.stdout)
    assert result["processed_jobs"] == 0
    assert result["jobs"] == []


def _live_model_args(workspace: Path, key_file: Path) -> tuple[str, ...]:
    return (
        "run-worker",
        "--workspace",
        str(workspace),
        "--once",
        "--allow-live-model",
        "--openrouter-api-key-file",
        str(key_file),
        "--model-period-limit-micros",
        "1000000",
        "--model-reservation-micros",
        "10000",
    )


def _runtime_workspace(tmp_path: Path, name: str) -> Path:
    workspace = tmp_path / name
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0
    return workspace


def test_live_model_rejects_group_readable_key_file(tmp_path: Path) -> None:
    workspace = _runtime_workspace(tmp_path, "runtime-model-permissions")
    key_file = tmp_path / "openrouter-readable.key"
    secret = "sk-or-v1-fake-permission-secret-000000"
    key_file.write_text(secret + "\n", encoding="utf-8")
    key_file.chmod(0o640)

    worker = _run(*_live_model_args(workspace, key_file))

    assert worker.returncode == 1
    assert json.loads(worker.stderr)["error"]["code"] == "secret_permissions_too_open"
    assert secret not in worker.stdout + worker.stderr


def test_live_model_invalid_credential_is_sanitized(tmp_path: Path) -> None:
    workspace = _runtime_workspace(tmp_path, "runtime-model-invalid-key")
    key_file = tmp_path / "openrouter-invalid.key"
    secret = "invalid credential must never be printed"
    key_file.write_text(secret + "\n", encoding="utf-8")
    key_file.chmod(0o600)

    worker = _run(*_live_model_args(workspace, key_file))

    assert worker.returncode == 1
    assert json.loads(worker.stderr)["error"]["code"] == "credential_invalid"
    assert secret not in worker.stdout + worker.stderr


def test_worker_env_file_commissions_single_user_runtime(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime-env"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0

    secret = "sk-or-v1-fake-env-secret-000000"
    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "\n".join(
            (
                f"PAPER_RADAR_WORKSPACE={workspace}",
                "PAPER_RADAR_ALLOW_LIVE_SOURCE=false",
                "PAPER_RADAR_ALLOW_LIVE_MODEL=true",
                "PAPER_RADAR_ALLOW_LIVE_MAIL=true",
                f"PAPER_RADAR_OPENROUTER_API_KEY={secret}",
                "PAPER_RADAR_MODEL_PERIOD_LIMIT_MICROS=1000000",
                "PAPER_RADAR_MODEL_RESERVATION_MICROS=10000",
                "PAPER_RADAR_RECIPIENT_EMAIL=reader@example.com",
                "PAPER_RADAR_SMTP_HOST=smtp.example.com",
                "PAPER_RADAR_SMTP_PORT=465",
                "PAPER_RADAR_SMTP_SENDER=paper-radar@example.com",
                "PAPER_RADAR_SMTP_USERNAME=mailer@example.com",
                "PAPER_RADAR_SMTP_PASSWORD=fake-smtp-password",
                "PAPER_RADAR_DIGEST_TIMEZONE=Asia/Taipei",
                "PAPER_RADAR_DIGEST_LOCAL_TIME=08:00",
                "PAPER_RADAR_DIGEST_MAX_ITEMS=5",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    enabled = _run(
        "effects",
        "enable",
        "--workspace",
        str(workspace),
    )
    assert enabled.returncode == 0, enabled.stdout + enabled.stderr

    worker = _run(
        "run-worker",
        "--env-file",
        str(env_file),
        "--once",
    )

    assert worker.returncode == 0, worker.stdout + worker.stderr
    result = json.loads(worker.stdout)
    assert result["processed_jobs"] == 0
    assert result["jobs"] == []
    assert secret not in worker.stdout + worker.stderr

    import sqlite3

    with sqlite3.connect(workspace / "state/app.sqlite3") as connection:
        row = connection.execute(
            "SELECT reader_id,channel,enabled,timezone,schedule_json,"
            "max_items,recipient_ref,policy_version "
            "FROM delivery_subscriptions"
        ).fetchone()

    assert row == (
        "local",
        "email",
        1,
        "Asia/Taipei",
        '{"kind":"daily","local_time":"08:00"}',
        5,
        "recipient:primary",
        1,
    )


def test_worker_env_file_bootstraps_mvp_profile_without_profile_cli(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime-profile-env"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0

    env_file = tmp_path / "profile.env"
    env_file.write_text(
        "\n".join(
            (
                f"PAPER_RADAR_WORKSPACE={workspace}",
                "PAPER_RADAR_PROFILE_DOMAINS=machine_learning,statistics",
                "PAPER_RADAR_PROFILE_SCOPE=關注機器學習與統計方法的新 arXiv 論文。",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    worker = _run(
        "run-worker",
        "--env-file",
        str(env_file),
        "--once",
    )

    assert worker.returncode == 0, worker.stdout + worker.stderr

    import sqlite3

    with sqlite3.connect(workspace / "state/app.sqlite3") as connection:
        profile = connection.execute(
            "SELECT reader_id,name,lifecycle,published_revision "
            "FROM watch_profiles WHERE id='personal'"
        ).fetchone()
        domains = connection.execute(
            "SELECT domain_id,domain_revision FROM watch_profile_domains "
            "WHERE profile_id='personal' AND revision=1 ORDER BY domain_id"
        ).fetchall()
        filters = connection.execute(
            "SELECT filters_json FROM watch_profile_revisions "
            "WHERE profile_id='personal' AND revision=1"
        ).fetchone()

    assert profile == ("local", "我的 arXiv 論文雷達", "active", 1)
    assert domains == [
        ("machine_learning", 1),
        ("statistics", 1),
    ]
    assert json.loads(filters[0])["sources"] == ["arxiv"]



def test_worker_env_live_mail_fails_fast_when_effects_disabled(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runtime-effects-off"
    initialized = _run(
        "init",
        "--workspace",
        str(workspace),
        "--with-runtime",
    )
    assert initialized.returncode == 0

    env_file = tmp_path / "mail.env"
    env_file.write_text(
        "\n".join(
            (
                f"PAPER_RADAR_WORKSPACE={workspace}",
                "PAPER_RADAR_ALLOW_LIVE_MAIL=true",
                "PAPER_RADAR_RECIPIENT_EMAIL=reader@example.com",
                "PAPER_RADAR_SMTP_HOST=smtp.example.com",
                "PAPER_RADAR_SMTP_PORT=465",
                "PAPER_RADAR_SMTP_SENDER=paper-radar@example.com",
                "PAPER_RADAR_SMTP_USERNAME=mailer@example.com",
                "PAPER_RADAR_SMTP_PASSWORD=fake-smtp-password",
                "PAPER_RADAR_DIGEST_TIMEZONE=Asia/Taipei",
                "PAPER_RADAR_DIGEST_LOCAL_TIME=08:00",
                "PAPER_RADAR_DIGEST_MAX_ITEMS=5",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    worker = _run(
        "run-worker",
        "--env-file",
        str(env_file),
        "--once",
    )

    assert worker.returncode == 1
    assert worker.stdout == ""
    error = json.loads(worker.stderr)["error"]
    assert error["code"] == "external_effects_disabled"
    assert "effects enable --env-file" in error["hint"]

    import sqlite3

    with sqlite3.connect(workspace / "state/app.sqlite3") as connection:
        subscriptions = connection.execute(
            "SELECT COUNT(*) FROM delivery_subscriptions"
        ).fetchone()[0]

    assert subscriptions == 0
