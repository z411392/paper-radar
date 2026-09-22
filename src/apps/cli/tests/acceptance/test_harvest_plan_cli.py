import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]


def cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def ok(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def failed(result: subprocess.CompletedProcess[str], code: str) -> None:
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == code
    assert "Traceback" not in result.stderr


def snapshot(workspace: Path) -> str:
    uri = (workspace / "state/app.sqlite3").as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return "\n".join(connection.iterdump())


def profile_file(tmp_path: Path, domain_id: str = "statistics") -> Path:
    payload = json.loads((ROOT / "config/watch-profile.example.json").read_text(encoding="utf-8"))
    payload["domains"] = [{"id": domain_id, "revision": 1}]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def prepare(tmp_path: Path, domain_id: str = "statistics") -> Path:
    workspace = tmp_path / "研究資料"
    info = ok(cli(tmp_path, "init", "--workspace", str(workspace), "--with-discovery"))
    assert info["schema_version"] == 4
    ok(
        cli(
            tmp_path,
            "domains",
            "import",
            "--workspace",
            str(workspace),
            "--file",
            str(ROOT / "config/domain-seeds.json"),
        )
    )
    ok(
        cli(
            tmp_path,
            "profile",
            "publish",
            "--workspace",
            str(workspace),
            "--file",
            str(profile_file(tmp_path, domain_id)),
        )
    )
    return workspace


def plan_args(workspace: Path, domain_id: str = "statistics") -> tuple[str, ...]:
    return (
        "harvest",
        "plan",
        "--workspace",
        str(workspace),
        "--profile-id",
        "personal",
        "--domain-id",
        domain_id,
        "--window-start",
        "2026-09-22T00:00:00+00:00",
        "--window-end",
        "2026-09-23T00:00:00+00:00",
        "--defer-unsupported",
    )


def test_discovery_upgrade_preserves_identity_and_profile_commands_still_work(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    before = ok(cli(tmp_path, "init", "--workspace", str(workspace), "--with-profiles"))
    assert before["schema_version"] == 2
    after = ok(cli(tmp_path, "init", "--workspace", str(workspace), "--with-discovery"))
    assert after["schema_version"] == 4
    assert after["workspace_id"] == before["workspace_id"]
    assert after["epoch"] == before["epoch"]
    ok(
        cli(
            tmp_path,
            "domains",
            "import",
            "--workspace",
            str(workspace),
            "--file",
            str(ROOT / "config/domain-seeds.json"),
        )
    )
    published = ok(
        cli(
            tmp_path,
            "profile",
            "publish",
            "--workspace",
            str(workspace),
            "--file",
            str(profile_file(tmp_path)),
        )
    )
    shown = ok(cli(tmp_path, "profile", "show", "--workspace", str(workspace), "--id", "personal"))
    assert shown == published


def test_harvest_plan_is_read_only_and_contains_exact_provenance(tmp_path: Path) -> None:
    workspace = prepare(tmp_path)
    before = snapshot(workspace)
    result = ok(cli(tmp_path, *plan_args(workspace)))
    assert result["source_id"] == "arxiv"
    assert "cat:stat.AP" in result["search_query"]
    assert "cat:stat.TH" in result["search_query"]
    assert len(result["query_fingerprint"]) == 64
    deferred = {item["name"] for item in result["deferred_filters"]}
    assert {"languages", "free_only", "scope_text"} <= deferred
    assert snapshot(workspace) == before
    with sqlite3.connect(workspace / "state/app.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM harvest_attempts").fetchone()[0] == 0


def test_plan_requires_explicit_deferred_filter_policy(tmp_path: Path) -> None:
    workspace = prepare(tmp_path)
    args = list(plan_args(workspace))
    args.remove("--defer-unsupported")
    before = snapshot(workspace)
    failed(cli(tmp_path, *args), "unsupported_filters")
    assert snapshot(workspace) == before


def test_badminton_is_not_silently_mapped_to_arxiv(tmp_path: Path) -> None:
    workspace = prepare(tmp_path, "badminton")
    failed(cli(tmp_path, *plan_args(workspace, "badminton")), "source_not_selected")


def test_profile_only_schema_is_not_implicitly_upgraded_by_plan(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    ok(cli(tmp_path, "init", "--workspace", str(workspace), "--with-profiles"))
    before = snapshot(workspace)
    failed(cli(tmp_path, *plan_args(workspace)), "schema_upgrade_required")
    assert snapshot(workspace) == before


def test_plan_argument_errors_do_not_create_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "absent"
    result = cli(
        tmp_path,
        "harvest",
        "plan",
        "--workspace",
        str(workspace),
        "--profile-id",
        "personal",
        "--domain-id",
        "statistics",
        "--window-start",
        "2026-09-22T00:00:01+00:00",
        "--window-end",
        "2026-09-23T00:00:00+00:00",
    )
    assert result.returncode == 2
    assert not workspace.exists()
