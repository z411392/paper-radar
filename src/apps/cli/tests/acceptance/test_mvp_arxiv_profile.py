import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from libs.kernel.adapters.driven.sqlite_connection_factory import (
    SqliteConnectionFactory,
)
from libs.research_workflow.adapters.driven.sqlite_scheduler_input_adapter import (
    SqliteSchedulerInputAdapter,
)


ROOT = Path(__file__).resolve().parents[5]


def cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "apps.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def ok(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    return json.loads(result.stdout)


def test_mvp_profile_schedules_requested_arxiv_and_pubmed_bindings(tmp_path: Path) -> None:
    workspace = tmp_path / "runtime"
    initialized = ok(
        cli(
            tmp_path,
            "init",
            "--workspace",
            str(workspace),
            "--with-runtime",
        )
    )
    assert initialized["schema_version"] == 24

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
            str(ROOT / "config/watch-profile.mvp.json"),
        )
    )
    assert published["filters"]["sources"] == ["arxiv", "pubmed"]

    snapshot = SqliteSchedulerInputAdapter(
        SqliteConnectionFactory(workspace).connect
    ).read(datetime.now(timezone.utc))

    assert snapshot.input_gaps == ()
    assert {
        (item.domain_id, item.source_id)
        for item in snapshot.harvest_bindings
    } == {
        ("deep_learning", "arxiv"),
        ("machine_learning", "arxiv"),
        ("statistics", "arxiv"),
        ("badminton", "pubmed"),
        ("male_reproductive_urology", "pubmed"),
    }
