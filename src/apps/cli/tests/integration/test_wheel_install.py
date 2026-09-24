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
    migration_names = [
        "0001-object-registry.sql",
        "0002-watch-profiles.sql",
        "0003-scholarly-catalog.sql",
        "0004-discovery.sql",
        "0005-paper-explanations.sql",
        "0006-retrieval.sql",
        "0007-delivery.sql",
        "0008-workflow-jobs.sql",
        "0009-relevance-assessment-domains.sql",
        "0010-pubmed-harvest.sql",
        "0011-crossref-harvest.sql",
        "0012-crossref-repair.sql",
        "0013-crossref-window-splits.sql",
    ]
    with zipfile.ZipFile(artifacts[0]) as archive:
        names = archive.namelist()
        packaged = {
            name: archive.read(f"libs/kernel/resources/migrations/{name}")
            for name in migration_names
        }
    for name in migration_names:
        assert packaged[name] == (root / "migrations" / name).read_bytes()
    assert "apps/cli/__main__.py" in names
    assert "libs/research_workflow/ports/read_runtime_version_port.py" in names
    assert not any("/tests/" in name or "__init__.py" in name for name in names)

    uv = shutil.which("uv")
    assert uv is not None, "Run this test through the locked uv environment"
    venv = tmp_path / "wheel-env"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env.update({"UV_PROJECT_ENVIRONMENT": str(venv), "UV_OFFLINE": "1", "UV_PYTHON": sys.executable})

    # The lock has artifact URLs; an installed cache need not have registry resolution metadata.
    # Install locked runtime dependencies without the project, then install only the built wheel.
    for command in [
        [uv, "venv", "--python", sys.executable, str(venv)],
        [uv, "sync", "--locked", "--offline", "--no-dev", "--no-install-project", "--project", str(root)],
        [uv, "pip", "install", "--offline", "--no-deps", "--python", str(python), str(artifacts[0])],
        [uv, "pip", "check", "--python", str(python)],
    ]:
        result = subprocess.run(
            command,
            cwd=tmp_path,
            env=env,
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
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["package_version"] == "0.1.0"
    location = subprocess.run(
        [str(python), "-I", "-c", "import apps.cli.entrypoints as e; print(e.__file__)"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert location.returncode == 0, location.stderr
    assert Path(location.stdout.strip()).resolve().is_relative_to(venv.resolve())
    assert list(cwd.iterdir()) == []

    workspace = tmp_path / "wheel workspace"
    snapshots = []
    for _ in range(2):
        initialized = subprocess.run(
            [
                str(python),
                "-I",
                "-m",
                "apps.cli",
                "init",
                "--workspace",
                str(workspace),
                "--with-runtime",
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert initialized.returncode == 0, initialized.stdout + initialized.stderr
        snapshots.append(json.loads(initialized.stdout))
    assert snapshots[0] == snapshots[1]
    assert snapshots[0]["schema_version"] == 13
    assert snapshots[0]["external_effects_enabled"] is False
    assert (workspace / "state/app.sqlite3").is_file()
    assert list(cwd.iterdir()) == []
