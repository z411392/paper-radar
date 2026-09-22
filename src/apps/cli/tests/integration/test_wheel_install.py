import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def test_built_wheel_runs_in_a_clean_noneditable_environment(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[5]
    wheels = tmp_path / "wheels"
    built = subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(wheels)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    artifacts = list(wheels.glob("*.whl"))
    assert len(artifacts) == 1
    with zipfile.ZipFile(artifacts[0]) as archive:
        names = archive.namelist()
    assert "apps/cli/__main__.py" in names
    assert "libs/research_workflow/ports/read_runtime_version_port.py" in names
    assert not any("/tests/" in name or "__init__.py" in name for name in names)

    uv = shutil.which("uv")
    assert uv is not None, "Run this test through the locked uv environment"
    venv = tmp_path / "wheel-env"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    for command in [
        [uv, "venv", "--python", sys.executable, str(venv)],
        [uv, "pip", "install", "--offline", "--python", str(python), str(artifacts[0])],
    ]:
        result = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    cwd = tmp_path / "unrelated-directory"
    cwd.mkdir()
    result = subprocess.run(
        [str(python), "-I", "-m", "apps.cli", "version"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["package_version"] == "0.1.0"
    assert list(cwd.iterdir()) == []
