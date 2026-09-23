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
    assert json.loads(initialized.stdout)["schema_version"] == 8

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


def test_launchd_template_uses_same_cli_and_does_not_enable_live_source_by_default() -> None:
    path = ROOT / "deploy/macos/com.paper-radar.worker.plist.example"
    root = ET.parse(path).getroot()
    text = ET.tostring(root, encoding="unicode")

    assert "apps.cli" in text
    assert "run-worker" in text
    assert "--with-runtime" not in text
    assert "--allow-live-source" not in text
    assert "__PYTHON__" in text
    assert "__WORKSPACE__" in text
