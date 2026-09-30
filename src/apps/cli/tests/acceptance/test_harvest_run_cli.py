import json
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


def run_args(workspace: Path) -> tuple[str, ...]:
    return (
        "harvest",
        "run",
        "--workspace",
        str(workspace),
        "--profile-id",
        "personal",
        "--domain-id",
        "statistics",
        "--window-start",
        "2026-09-22T00:00:00+00:00",
        "--window-end",
        "2026-09-23T00:00:00+00:00",
        "--defer-unsupported",
        "--max-pages",
        "1",
    )


def test_run_requires_explicit_live_authorization_before_workspace_access(tmp_path: Path) -> None:
    workspace = tmp_path / "must-not-exist"
    result = cli(tmp_path, *run_args(workspace))
    assert result.returncode == 1
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "live_source_not_authorized"
    assert not workspace.exists()


def test_live_run_requires_explicit_shared_gate_path_before_workspace_access(tmp_path: Path) -> None:
    workspace = tmp_path / "must-not-exist"
    result = cli(tmp_path, *run_args(workspace), "--allow-live-source")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "rate-limit-state" in result.stderr
    assert not workspace.exists()
