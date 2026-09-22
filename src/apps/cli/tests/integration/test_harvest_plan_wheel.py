import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def test_discovery_migrations_are_bundled_in_installed_wheel(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[5]
    uv = shutil.which("uv")
    assert uv is not None
    dist = tmp_path / "dist"
    venv = tmp_path / "installed"
    python = venv / "bin/python"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env.update({"UV_PROJECT_ENVIRONMENT": str(venv), "UV_OFFLINE": "1", "UV_PYTHON": sys.executable})

    def execute(command: list[str], cwd: Path = tmp_path) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    execute([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(dist)], root)
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for name in ("0003-scholarly-catalog.sql", "0004-discovery.sql"):
            expected = (root / "migrations" / name).read_bytes()
            assert archive.read("libs/kernel/resources/migrations/" + name) == expected

    execute([uv, "venv", "--python", sys.executable, str(venv)])
    execute(
        [uv, "sync", "--locked", "--offline", "--no-dev", "--no-install-project", "--project", str(root)]
    )
    execute([uv, "pip", "install", "--offline", "--no-deps", "--python", str(python), str(wheel)])
    outside = tmp_path / "outside"
    outside.mkdir()
    result = execute(
        [
            str(python),
            "-I",
            "-c",
            (
                "from libs.kernel.adapters.driven.bundled_workspace_migrations "
                "import load_workspace_migrations; "
                "print(','.join(m.name for m in load_workspace_migrations(with_discovery=True)))"
            ),
        ],
        outside,
    )
    assert result.stdout.strip().split(",")[-2:] == [
        "0003-scholarly-catalog.sql",
        "0004-discovery.sql",
    ]
