import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"PAGER": "cat", "TERM": "dumb"})
    return subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", *arguments],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_version_works_outside_repository_without_path_injection(tmp_path: Path) -> None:
    result = run_cli(tmp_path, "version")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["package_name"] == "paper-radar"
    assert payload["package_version"] == "0.1.0"
    assert payload["python_version"] == ".".join(map(str, sys.version_info[:3]))
    assert sorted(payload) == ["package_name", "package_version", "python_version"]
    assert list(tmp_path.iterdir()) == []


def test_unknown_command_is_not_success(tmp_path: Path) -> None:
    result = run_cli(tmp_path, "not-a-command")
    assert result.returncode != 0
    assert result.stdout.strip() == ""
    assert list(tmp_path.iterdir()) == []


def test_no_command_lists_only_available_commands(tmp_path: Path) -> None:
    result = run_cli(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "version" in result.stdout + result.stderr
    assert "initialize-workspace" not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


def test_import_does_not_invoke_cli_or_create_workspace(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import apps.cli.__main__; import apps.cli.entrypoints"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert list(tmp_path.iterdir()) == []
