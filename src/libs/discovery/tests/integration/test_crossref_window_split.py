import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.sqlite_crossref_harvest_journal_adapter import (
    SqliteCrossrefHarvestJournalAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_repair_store_adapter import (
    SqliteCrossrefRepairStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_window_split_store_adapter import (
    SqliteCrossrefWindowSplitStoreAdapter,
)
from libs.discovery.application.commands.split_crossref_window import SplitCrossrefWindow
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.dtos.crossref_repair import CrossrefRepairPolicy
from libs.discovery.exceptions.crossref_window_split_error import (
    CrossrefWindowSplitError,
)


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
START = NOW - timedelta(days=3)
END = START + timedelta(days=1)
MID = START + timedelta(hours=12)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "split.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0001-object-registry.sql",
        "0011-crossref-harvest.sql",
        "0012-crossref-repair.sql",
        "0013-crossref-window-splits.sql",
    ):
        connection.executescript((root / "migrations" / name).read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO workspace_metadata VALUES(1,'workspace:test',1,0,?,NULL)",
        (NOW.isoformat(),),
    )
    connection.commit()
    connection.close()
    return (
        path,
        SqliteCrossrefHarvestJournalAdapter(_connect(path)),
        SqliteCrossrefRepairStoreAdapter(_connect(path)),
        SqliteCrossrefWindowSplitStoreAdapter(_connect(path)),
    )


def _plan(start=START, end=END):
    source = CrossrefSourceAdapter()
    return source, source.compile(
        CrossrefWindowInput(
            binding_key="personal:3:statistics:1:crossref",
            scope_query="statistics",
            from_index=start,
            until_index=end,
            contact_email="fixture@example.invalid",
            config_version="v1",
            rows=1000,
        )
    )


def _complete(path: Path, journal, plan):
    window = journal.ensure_window(plan, NOW)
    state = journal.start_pass(plan, NOW)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE crossref_harvest_passes SET "
        "state='completed',current_cursor=NULL,traversal_complete=1,"
        "accounting_complete=1,repair_pending=0,drift_suspected=0,"
        "parse_gap_count=0,source_completeness='provisional',finished_at=? "
        "WHERE id=?",
        (NOW.isoformat(), state.pass_id),
    )
    connection.execute(
        "UPDATE crossref_harvest_windows SET state='traversed',updated_at=? WHERE id=?",
        (NOW.isoformat(), window.window_id),
    )
    connection.commit()
    connection.close()
    return window


def _policy():
    return CrossrefRepairPolicy(
        safety_lag_seconds=0,
        lookback_windows=3,
        periodic_repair_after_seconds=86400,
        max_windows=4,
    )


def test_split_preserves_exact_inclusive_boundary_and_parent_history(tmp_path: Path) -> None:
    path, journal, _, split_store = _setup(tmp_path)
    source, plan = _plan()
    parent = journal.ensure_window(plan, NOW)
    running = journal.start_pass(plan, NOW)
    journal.fail_pass(running.pass_id, "window_split", NOW)

    result = SplitCrossrefWindow(source, journal, split_store)(
        plan,
        split_at=MID,
        reason="volume_budget",
        created_at=NOW,
    )

    assert result.parent_window_id == parent.window_id
    assert result.left_from == START
    assert result.left_until == MID
    assert result.right_from == MID
    assert result.right_until == END

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT count(*) FROM crossref_harvest_windows"
    ).fetchone()[0] == 3
    assert connection.execute(
        "SELECT state,error_code FROM crossref_harvest_passes WHERE id=?",
        (running.pass_id,),
    ).fetchone() == ("failed", "window_split")
    assert connection.execute(
        "SELECT split_at,reason FROM crossref_window_splits WHERE parent_window_id=?",
        (parent.window_id,),
    ).fetchone() == (MID.isoformat(), "volume_budget")
    connection.close()


def test_running_parent_must_be_failed_before_split(tmp_path: Path) -> None:
    _, journal, _, split_store = _setup(tmp_path)
    source, plan = _plan()
    journal.ensure_window(plan, NOW)
    journal.start_pass(plan, NOW)

    with pytest.raises(
        CrossrefWindowSplitError,
        match="crossref_split_parent_running",
    ):
        SplitCrossrefWindow(source, journal, split_store)(
            plan,
            split_at=MID,
            reason="page_budget",
            created_at=NOW,
        )


def test_finalized_parent_cannot_be_split_after_watermark_evidence(tmp_path: Path) -> None:
    path, journal, repair, split_store = _setup(tmp_path)
    source, plan = _plan()
    parent = _complete(path, journal, plan)
    repair.finalize_window(
        plan,
        parent.window_id,
        finalized_at=NOW,
        policy=_policy(),
    )

    with pytest.raises(
        CrossrefWindowSplitError,
        match="crossref_split_parent_finalized",
    ):
        SplitCrossrefWindow(source, journal, split_store)(
            plan,
            split_at=MID,
            reason="operator",
            created_at=NOW,
        )


def test_watermark_uses_leaf_children_and_never_requires_split_parent(
    tmp_path: Path,
) -> None:
    path, journal, repair, split_store = _setup(tmp_path)
    source, parent_plan = _plan()
    parent = journal.ensure_window(parent_plan, NOW)
    running = journal.start_pass(parent_plan, NOW)
    journal.fail_pass(running.pass_id, "window_split", NOW)
    result = SplitCrossrefWindow(source, journal, split_store)(
        parent_plan,
        split_at=MID,
        reason="volume_budget",
        created_at=NOW,
    )

    _, left_plan = _plan(START, MID)
    _, right_plan = _plan(MID, END)
    _complete(path, journal, left_plan)
    _complete(path, journal, right_plan)

    repair.finalize_window(
        right_plan,
        result.right_window_id,
        finalized_at=NOW,
        policy=_policy(),
    )
    assert repair.read_watermark(parent_plan) is None

    repair.finalize_window(
        left_plan,
        result.left_window_id,
        finalized_at=NOW,
        policy=_policy(),
    )
    watermark = repair.read_watermark(parent_plan)
    assert watermark is not None
    assert watermark.finalized_until == END
    assert watermark.latest_window_id == result.right_window_id
    assert repair.list_candidates(parent_plan, now=NOW, policy=_policy()) == ()
