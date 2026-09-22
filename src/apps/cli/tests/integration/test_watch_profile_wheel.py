import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def test_installed_wheel_manages_profiles_outside_repository(tmp_path: Path) -> None:
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

    def execute(command: list[str], cwd: Path = tmp_path) -> str:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=60, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    execute([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-t", "sdist", "-d", str(dist)], root)
    wheel = next(dist.glob("*.whl"))
    sql = (root / "migrations/0002-watch-profiles.sql").read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("libs/kernel/resources/migrations/0002-watch-profiles.sql") == sql
    with tarfile.open(next(dist.glob("*.tar.gz"))) as archive:
        matches = [n for n in archive.getnames() if n.endswith("/migrations/0002-watch-profiles.sql")]
        assert len(matches) == 1
        stream = archive.extractfile(matches[0])
        assert stream is not None and stream.read() == sql
    execute([uv, "venv", "--python", sys.executable, str(venv)])
    execute([uv, "sync", "--locked", "--offline", "--no-dev", "--no-install-project", "--project", str(root)])
    execute([uv, "pip", "install", "--offline", "--no-deps", "--python", str(python), str(wheel)])
    execute([uv, "pip", "check", "--python", str(python)])
    cwd = tmp_path / "not-the-repository"
    cwd.mkdir()
    workspace = cwd / "閱讀 資料"
    # Only explicit input files are copied. There is no migrations/ directory in this cwd.
    seeds = cwd / "domains.json"
    profile = cwd / "profile.json"
    seeds.write_bytes((root / "config/domain-seeds.json").read_bytes())
    profile.write_bytes((root / "config/watch-profile.example.json").read_bytes())
    location = execute([str(python), "-I", "-c", "import apps.cli.entrypoints as e; print(e.__file__)"], cwd)
    assert Path(location.strip()).resolve().is_relative_to(venv.resolve())
    prefix = [str(python), "-I", "-m", "apps.cli"]
    first = json.loads(execute([*prefix, "init", "--workspace", str(workspace), "--with-profiles"], cwd))
    execute([*prefix, "domains", "import", "--workspace", str(workspace), "--file", str(seeds)], cwd)
    published = json.loads(execute([*prefix, "profile", "publish", "--workspace", str(workspace),
                                    "--file", str(profile)], cwd))
    assert published["revision"] == 1 and len(published["domains"]) == 5
    read = json.loads(execute([*prefix, "profile", "show", "--workspace", str(workspace),
                              "--id", "personal"], cwd))
    assert read == published
    second = json.loads(execute([*prefix, "init", "--workspace", str(workspace), "--with-profiles"], cwd))
    assert first == second and first["schema_version"] == 2
