import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[5]


def cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", *args],
        cwd=cwd, capture_output=True, text=True, timeout=20, check=False,
    )


def ok(result: subprocess.CompletedProcess[str]):
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def failed(result: subprocess.CompletedProcess[str], code: str) -> None:
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == code
    assert "Traceback" not in result.stderr


def snapshot(workspace: Path) -> str:
    with sqlite3.connect((workspace / "state/app.sqlite3").as_uri() + "?mode=ro", uri=True) as connection:
        return "\n".join(connection.iterdump())


def write_profile(tmp_path: Path, scope: str = "只看機器學習") -> Path:
    document = json.loads((ROOT / "config/watch-profile.example.json").read_text(encoding="utf-8"))
    document["scope_text"] = scope
    document["domains"] = [{"id": "machine_learning", "revision": 1}]
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "研究 資料"
    info = ok(cli(tmp_path, "init", "--workspace", str(target), "--with-profiles"))
    assert info["schema_version"] == 2
    assert info["external_effects_enabled"] is False
    seeds = ok(cli(tmp_path, "domains", "import", "--workspace", str(target),
                   "--file", str(ROOT / "config/domain-seeds.json")))
    assert len(seeds["domains"]) == 5
    return target


def test_schema_upgrade_is_explicit_and_preserves_identity(tmp_path: Path) -> None:
    target = tmp_path / "workspace"
    before = ok(cli(tmp_path, "init", "--workspace", str(target)))
    original = snapshot(target)
    failed(cli(tmp_path, "profile", "show", "--workspace", str(target), "--id", "personal"),
           "schema_upgrade_required")
    assert snapshot(target) == original
    after = ok(cli(tmp_path, "init", "--workspace", str(target), "--with-profiles"))
    assert after["schema_version"] == 2
    assert after["workspace_id"] == before["workspace_id"]
    assert after["epoch"] == before["epoch"]
    again = ok(cli(tmp_path, "init", "--workspace", str(target), "--with-profiles"))
    assert again == after
    upgraded = snapshot(target)
    failed(cli(tmp_path, "init", "--workspace", str(target)), "unsupported_schema")
    assert snapshot(target) == upgraded


def test_publish_show_and_delayed_replay_do_not_undo_current(workspace: Path, tmp_path: Path) -> None:
    path = write_profile(tmp_path)
    original = path.read_text()
    args = ("profile", "publish", "--workspace", str(workspace), "--file", str(path))
    first = ok(cli(tmp_path, *args))
    assert first["revision"] == first["current_revision"] == 1
    assert first["domains"] == [{"id": "machine_learning", "revision": 1}]
    write_profile(tmp_path, "第二次設定")
    second = ok(cli(tmp_path, *args, "--expected-revision", "1"))
    assert second["revision"] == second["current_revision"] == 2
    path.write_text(original, encoding="utf-8")
    delayed = ok(cli(tmp_path, *args))
    assert delayed["revision"] == 1 and delayed["current_revision"] == 2
    current = ok(cli(tmp_path, "profile", "show", "--workspace", str(workspace), "--id", "personal"))
    historic = ok(cli(tmp_path, "profile", "show", "--workspace", str(workspace),
                      "--id", "personal", "--revision", "1"))
    assert current["scope_text"] == "第二次設定"
    assert historic["scope_text"] == "只看機器學習"
    assert historic["current_revision"] == 2
    assert isinstance(current["filters"], dict)
    assert "filters_json" not in current


def test_pause_publish_and_explicit_resume(workspace: Path, tmp_path: Path) -> None:
    path = write_profile(tmp_path)
    ok(cli(tmp_path, "profile", "publish", "--workspace", str(workspace), "--file", str(path)))
    result = ok(cli(tmp_path, "profile", "pause", "--workspace", str(workspace), "--id", "personal"))
    assert result["committed"] is True and result["lifecycle_requested"] == "paused"
    write_profile(tmp_path, "暫停時更新設定")
    updated = ok(cli(tmp_path, "profile", "publish", "--workspace", str(workspace),
                     "--file", str(path), "--expected-revision", "1"))
    assert updated["lifecycle"] == "paused"
    ok(cli(tmp_path, "profile", "resume", "--workspace", str(workspace), "--id", "personal"))
    current = ok(cli(tmp_path, "profile", "show", "--workspace", str(workspace), "--id", "personal"))
    assert current["lifecycle"] == "active" and current["revision"] == 2


def test_unknown_profile_is_not_an_empty_success(workspace: Path, tmp_path: Path) -> None:
    failed(cli(tmp_path, "profile", "show", "--workspace", str(workspace), "--id", "missing"),
           "profile_missing")


def test_stale_different_publication_is_rejected(workspace: Path, tmp_path: Path) -> None:
    path = write_profile(tmp_path)
    args = ("profile", "publish", "--workspace", str(workspace), "--file", str(path))
    ok(cli(tmp_path, *args))
    write_profile(tmp_path, "不同的新內容")
    before = snapshot(workspace)
    failed(cli(tmp_path, *args), "revision_conflict")
    assert snapshot(workspace) == before


def test_seed_replay_preserves_all_published_rows(workspace: Path, tmp_path: Path) -> None:
    path = write_profile(tmp_path)
    ok(cli(tmp_path, "profile", "publish", "--workspace", str(workspace), "--file", str(path)))
    before = snapshot(workspace)
    result = ok(cli(tmp_path, "domains", "import", "--workspace", str(workspace),
                    "--file", str(ROOT / "config/domain-seeds.json")))
    assert all(item["disposition"] == "unchanged" for item in result["domains"])
    assert snapshot(workspace) == before


@pytest.mark.parametrize("payload,code", [
    (b"{", "invalid_json"),
    (b'{"domains":[],"domains":[]}', "duplicate_key"),
    (b"\xffprivate input", "invalid_utf8"),
    (b" " * 1_000_001, "configuration_too_large"),
], ids=["malformed-json", "duplicate-keys", "invalid-utf8", "oversized-file"])
def test_bad_file_does_not_modify_database(workspace: Path, tmp_path: Path, payload: bytes, code: str) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(payload)
    before = snapshot(workspace)
    failed(cli(tmp_path, "domains", "import", "--workspace", str(workspace), "--file", str(path)), code)
    assert snapshot(workspace) == before


def test_missing_file_does_not_modify_database(workspace: Path, tmp_path: Path) -> None:
    before = snapshot(workspace)
    failed(cli(tmp_path, "profile", "publish", "--workspace", str(workspace),
               "--file", str(tmp_path / "missing.json")), "configuration_io_error")
    assert snapshot(workspace) == before


def test_fifo_is_not_read_as_unbounded_stream(workspace: Path, tmp_path: Path) -> None:
    path = tmp_path / "fifo"
    os.mkfifo(path)
    before = snapshot(workspace)
    failed(cli(tmp_path, "domains", "import", "--workspace", str(workspace), "--file", str(path)),
           "configuration_not_regular")
    assert snapshot(workspace) == before


@pytest.mark.parametrize("args", [
    ("profile", "show", "--id", "personal"),
    ("profile", "publish", "--file", "missing.json", "--expected-revision", "0"),
    ("profile", "show", "--id", "personal", "--revision", "9223372036854775808"),
    ("profile", "show", "--id", "personal", "--revision", "True"),
    ("profile", "show", "--id", "personal", "--unexpected", "value"),
    ("profile", "pause", "--id", "bad id"),
    ("domains", "import"),
])
def test_invalid_arguments_do_not_create_workspace(tmp_path: Path, args: tuple[str, ...]) -> None:
    workspace = tmp_path / "must-not-exist"
    # The first case intentionally lacks --workspace; others append it to test full prevalidation.
    supplied = args if args == ("profile", "show", "--id", "personal") else (
        *args, "--workspace", str(workspace)
    )
    result = cli(tmp_path, *supplied)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert not workspace.exists()


def test_show_missing_workspace_does_not_bootstrap(tmp_path: Path) -> None:
    workspace = tmp_path / "absent"
    failed(cli(tmp_path, "profile", "show", "--workspace", str(workspace), "--id", "personal"),
           "workspace_missing")
    assert not workspace.exists()


@pytest.mark.parametrize("args", [("--help",), ("profile", "--help"), ("domains", "import", "--help")])
def test_help_is_available_without_workspace(tmp_path: Path, args: tuple[str, ...]) -> None:
    result = cli(tmp_path, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "workspace" in result.stdout.lower() or "profile" in result.stdout.lower()
    assert list(tmp_path.iterdir()) == []


def test_invalid_extra_init_argument_is_rejected_before_upgrade(tmp_path: Path) -> None:
    workspace = tmp_path / "absent"
    result = cli(tmp_path, "init", "--workspace", str(workspace), "--with-profiles", "extra")
    assert result.returncode == 2 and not workspace.exists()
