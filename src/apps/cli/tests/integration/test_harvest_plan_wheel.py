import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def test_installed_wheel_can_initialize_plan_and_reject_unauthorized_live_run(
    tmp_path: Path,
) -> None:
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

    def invoke(command: list[str], cwd: Path = tmp_path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )

    def execute(command: list[str], cwd: Path = tmp_path) -> subprocess.CompletedProcess[str]:
        result = invoke(command, cwd)
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    execute([sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(dist)], root)
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for name in ("0003-scholarly-catalog.sql", "0004-discovery.sql"):
            expected = (root / "migrations" / name).read_bytes()
            assert archive.read("libs/kernel/resources/migrations/" + name) == expected

    execute([uv, "venv", "--python", sys.executable, str(venv)])
    execute([uv, "sync", "--locked", "--offline", "--no-dev", "--no-install-project", "--project", str(root)])
    execute([uv, "pip", "install", "--offline", "--no-deps", "--python", str(python), str(wheel)])

    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = outside / "workspace"
    domains = outside / "domains.json"
    profile = outside / "profile.json"
    domains.write_text(
        json.dumps(
            {
                "domains": [
                    {
                        "id": "statistics",
                        "name": "統計學",
                        "aliases": ["statistics"],
                        "include": ["statistics"],
                        "exclude": [],
                        "sources": ["arxiv"],
                        "source_categories": {"arxiv": ["stat.ML"]},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    profile.write_text(
        json.dumps(
            {
                "id": "personal",
                "reader_id": "local",
                "name": "測試閱讀",
                "scope_text": "只看統計研究",
                "domains": [{"id": "statistics", "revision": 1}],
                "filters": {
                    "include": [],
                    "exclude": [],
                    "languages": [],
                    "sources": ["arxiv"],
                    "free_only": False,
                    "allow_preprints": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    execute(
        [
            str(python),
            "-I",
            "-m",
            "apps.cli",
            "init",
            "--workspace",
            str(workspace),
            "--with-discovery",
        ],
        outside,
    )
    execute(
        [
            str(python),
            "-I",
            "-m",
            "apps.cli",
            "domains",
            "import",
            "--workspace",
            str(workspace),
            "--file",
            str(domains),
        ],
        outside,
    )
    execute(
        [
            str(python),
            "-I",
            "-m",
            "apps.cli",
            "profile",
            "publish",
            "--workspace",
            str(workspace),
            "--file",
            str(profile),
        ],
        outside,
    )
    common = [
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
    ]
    planned = execute(
        [str(python), "-I", "-m", "apps.cli", "harvest", "plan", *common],
        outside,
    )
    plan = json.loads(planned.stdout)
    assert plan["source_id"] == "arxiv"
    assert "cat:stat.ML" in plan["search_query"]
    assert len(plan["query_fingerprint"]) == 64

    blocked = invoke(
        [str(python), "-I", "-m", "apps.cli", "harvest", "run", *common, "--max-pages", "1"],
        outside,
    )
    assert blocked.returncode == 1 and blocked.stdout == ""
    assert json.loads(blocked.stderr)["error"]["code"] == "live_source_not_authorized"

    counted = execute(
        [
            str(python),
            "-I",
            "-c",
            (
                "import sqlite3,sys; "
                "c=sqlite3.connect(sys.argv[1]); "
                "print(c.execute('SELECT COUNT(*) FROM harvest_attempts').fetchone()[0])"
            ),
            str(workspace / "state/app.sqlite3"),
        ],
        outside,
    )
    assert counted.stdout.strip() == "0"
