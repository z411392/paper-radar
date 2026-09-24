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
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.dtos.crossref_repair import CrossrefRepairPolicy
from libs.discovery.exceptions.crossref_repair_error import CrossrefRepairError


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _connect(path: Path):
    def factory() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    return factory


def _setup(tmp_path: Path):
    path = tmp_path / "repair.sqlite3"
    root = Path(__file__).resolve().parents[5]
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    for name in (
        "0001-object-registry.sql",
        "0011-crossref-harvest.sql",
        "0012-crossref-repair.sql",
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
    )


def _plan(start: datetime, end: datetime, *, scope="statistics", config="v1"):
    source = CrossrefSourceAdapter()
    return source.compile(
        CrossrefWindowInput(
            binding_key="personal:3:statistics:1:crossref",
            scope_query=scope,
            from_index=start,
            until_index=end,
            contact_email="fixture@example.invalid",
            config_version=config,
            rows=1000,
        )
    )


def _completed_window(
    path: Path,
    journal,
    plan,
    *,
    repair_pending: bool = False,
    finished_at: datetime = NOW,
):
    window = journal.ensure_window(plan, NOW)
    state = journal.start_pass(plan, NOW)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE crossref_harvest_passes SET "
        "state='completed',current_cursor=NULL,traversal_complete=1,"
        "accounting_complete=1,source_completeness=?,repair_pending=?,"
        "drift_suspected=?,parse_gap_count=?,finished_at=? WHERE id=?",
        (
            "unknown" if repair_pending else "provisional",
            1 if repair_pending else 0,
            1 if repair_pending else 0,
            1 if repair_pending else 0,
            finished_at.isoformat(),
            state.pass_id,
        ),
    )
    connection.execute(
        "UPDATE crossref_harvest_windows SET state=?,updated_at=? WHERE id=?",
        (
            "repair_pending" if repair_pending else "traversed",
            finished_at.isoformat(),
            window.window_id,
        ),
    )
    connection.commit()
    connection.close()
    return window, state


def policy(**changes):
    values = {
        "safety_lag_seconds": 3600,
        "lookback_windows": 3,
        "periodic_repair_after_seconds": 86400,
        "max_windows": 4,
    }
    values.update(changes)
    return CrossrefRepairPolicy(**values)


def test_stream_identity_rejects_scope_change_without_new_config_generation(
    tmp_path: Path,
) -> None:
    _, _, store = _setup(tmp_path)
    first = _plan(NOW - timedelta(days=3), NOW - timedelta(days=2))
    changed = _plan(
        NOW - timedelta(days=2),
        NOW - timedelta(days=1),
        scope="machine learning",
    )

    store.ensure_stream(first, NOW)

    with pytest.raises(CrossrefRepairError, match="crossref_stream_definition_conflict"):
        store.ensure_stream(changed, NOW)


def test_candidates_prioritize_explicit_repair_and_respect_safety_lag_and_limit(
    tmp_path: Path,
) -> None:
    path, journal, store = _setup(tmp_path)
    old_clean = _plan(NOW - timedelta(days=4), NOW - timedelta(days=3))
    explicit = _plan(NOW - timedelta(days=3), NOW - timedelta(days=2))
    recent = _plan(
        NOW - timedelta(hours=2),
        NOW - timedelta(minutes=30),
    )
    clean_window, _ = _completed_window(
        path,
        journal,
        old_clean,
        finished_at=NOW - timedelta(days=2, hours=1),
    )
    explicit_window, _ = _completed_window(
        path,
        journal,
        explicit,
        repair_pending=True,
    )
    _completed_window(path, journal, recent, repair_pending=True)

    store.finalize_window(
        old_clean,
        clean_window.window_id,
        finalized_at=NOW - timedelta(days=2),
        policy=policy(safety_lag_seconds=0),
    )

    candidates = store.list_candidates(
        explicit,
        now=NOW,
        policy=policy(max_windows=2),
    )

    assert [(item.window_id, item.reason) for item in candidates] == [
        (explicit_window.window_id, "repair_pending"),
        (clean_window.window_id, "periodic_recent_window"),
    ]


def test_repair_starts_new_pass_generation_without_overwriting_live_pass(
    tmp_path: Path,
) -> None:
    path, journal, store = _setup(tmp_path)
    plan = _plan(NOW - timedelta(days=3), NOW - timedelta(days=2))
    window, live = _completed_window(path, journal, plan, repair_pending=True)

    repair = store.start_repair(
        plan,
        window.window_id,
        reason="repair_pending",
        started_at=NOW,
    )

    assert repair.pass_id != live.pass_id
    assert repair.pass_no == live.pass_no + 1
    assert repair.state == "running"
    assert repair.reason == "repair_pending"

    connection = sqlite3.connect(path)
    rows = connection.execute(
        "SELECT id,state,current_cursor FROM crossref_harvest_passes "
        "WHERE window_id=? ORDER BY pass_no",
        (window.window_id,),
    ).fetchall()
    connection.close()
    assert rows == [
        (live.pass_id, "completed", None),
        (repair.pass_id, "running", "*"),
    ]


def test_safety_lag_blocks_finalization(tmp_path: Path) -> None:
    path, journal, store = _setup(tmp_path)
    plan = _plan(NOW - timedelta(hours=2), NOW - timedelta(minutes=30))
    window, _ = _completed_window(path, journal, plan)

    with pytest.raises(CrossrefRepairError, match="crossref_window_inside_safety_lag"):
        store.finalize_window(
            plan,
            window.window_id,
            finalized_at=NOW,
            policy=policy(safety_lag_seconds=3600),
        )


def test_watermark_advances_only_through_contiguous_finalized_windows(
    tmp_path: Path,
) -> None:
    path, journal, store = _setup(tmp_path)
    p1 = _plan(NOW - timedelta(days=4), NOW - timedelta(days=3))
    p2 = _plan(NOW - timedelta(days=3), NOW - timedelta(days=2))
    p3 = _plan(NOW - timedelta(days=2), NOW - timedelta(days=1))
    w1, _ = _completed_window(path, journal, p1)
    w2, _ = _completed_window(path, journal, p2)
    w3, _ = _completed_window(path, journal, p3)
    zero_lag = policy(safety_lag_seconds=0)

    store.finalize_window(p1, w1.window_id, finalized_at=NOW, policy=zero_lag)
    first = store.read_watermark(p1)
    assert first is not None
    assert first.finalized_until == p1.definition.until_index

    store.finalize_window(p3, w3.window_id, finalized_at=NOW, policy=zero_lag)
    blocked = store.read_watermark(p1)
    assert blocked is not None
    assert blocked.finalized_until == p1.definition.until_index

    store.finalize_window(p2, w2.window_id, finalized_at=NOW, policy=zero_lag)
    advanced = store.read_watermark(p1)
    assert advanced is not None
    assert advanced.finalized_until == p3.definition.until_index


def test_completed_clean_repair_can_append_new_finalization_generation(
    tmp_path: Path,
) -> None:
    path, journal, store = _setup(tmp_path)
    plan = _plan(NOW - timedelta(days=3), NOW - timedelta(days=2))
    window, _ = _completed_window(path, journal, plan, repair_pending=True)
    repair = store.start_repair(
        plan,
        window.window_id,
        reason="repair_pending",
        started_at=NOW,
    )

    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE crossref_harvest_passes SET "
        "state='completed',current_cursor=NULL,traversal_complete=1,"
        "accounting_complete=1,repair_pending=0,drift_suspected=0,"
        "parse_gap_count=0,source_completeness='provisional',finished_at=? "
        "WHERE id=?",
        (NOW.isoformat(), repair.pass_id),
    )
    connection.execute(
        "UPDATE crossref_harvest_windows SET state='traversed',updated_at=? "
        "WHERE id=?",
        (NOW.isoformat(), window.window_id),
    )
    connection.commit()
    connection.close()

    reconciled = store.reconcile_repair(repair.repair_id, NOW)
    finalization = store.finalize_window(
        plan,
        window.window_id,
        finalized_at=NOW,
        policy=policy(safety_lag_seconds=0),
    )

    assert reconciled.state == "completed"
    assert finalization.generation == 1
    assert finalization.pass_id == repair.pass_id


def test_failed_repair_never_creates_or_advances_watermark(tmp_path: Path) -> None:
    path, journal, store = _setup(tmp_path)
    plan = _plan(NOW - timedelta(days=3), NOW - timedelta(days=2))
    window, _ = _completed_window(path, journal, plan, repair_pending=True)
    repair = store.start_repair(
        plan,
        window.window_id,
        reason="repair_pending",
        started_at=NOW,
    )
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE crossref_harvest_passes SET state='failed',error_code='timeout',"
        "finished_at=? WHERE id=?",
        (NOW.isoformat(), repair.pass_id),
    )
    connection.commit()
    connection.close()

    reconciled = store.reconcile_repair(repair.repair_id, NOW)

    assert reconciled.state == "failed"
    assert store.read_watermark(plan) is None
    with pytest.raises(CrossrefRepairError, match="crossref_window_not_finalizable"):
        store.finalize_window(
            plan,
            window.window_id,
            finalized_at=NOW,
            policy=policy(safety_lag_seconds=0),
        )


def test_failed_repair_cooldown_prevents_maintenance_hot_loop(tmp_path: Path) -> None:
    path, journal, store = _setup(tmp_path)
    plan = _plan(NOW - timedelta(days=3), NOW - timedelta(days=2))
    window, _ = _completed_window(path, journal, plan, repair_pending=True)
    repair = store.start_repair(
        plan,
        window.window_id,
        reason="repair_pending",
        started_at=NOW,
    )
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE crossref_harvest_passes SET state='failed',error_code='timeout',"
        "finished_at=? WHERE id=?",
        (NOW.isoformat(), repair.pass_id),
    )
    connection.commit()
    connection.close()
    store.reconcile_repair(repair.repair_id, NOW)

    early = store.list_candidates(
        plan,
        now=NOW + timedelta(minutes=59),
        policy=policy(repair_retry_after_seconds=3600),
    )
    due = store.list_candidates(
        plan,
        now=NOW + timedelta(hours=1),
        policy=policy(repair_retry_after_seconds=3600),
    )

    assert early == ()
    assert [(item.window_id, item.reason) for item in due] == [
        (window.window_id, "repair_pending")
    ]


def test_consecutive_failed_repairs_hit_visible_cap(tmp_path: Path) -> None:
    path, journal, store = _setup(tmp_path)
    plan = _plan(NOW - timedelta(days=3), NOW - timedelta(days=2))
    window, _ = _completed_window(path, journal, plan, repair_pending=True)

    for index in range(2):
        started = NOW + timedelta(hours=index)
        repair = store.start_repair(
            plan,
            window.window_id,
            reason="repair_pending",
            started_at=started,
        )
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE crossref_harvest_passes SET state='failed',"
            "error_code='timeout',finished_at=? WHERE id=?",
            ((started + timedelta(minutes=1)).isoformat(), repair.pass_id),
        )
        connection.commit()
        connection.close()
        store.reconcile_repair(
            repair.repair_id,
            started + timedelta(minutes=1),
        )

    with pytest.raises(
        CrossrefRepairError,
        match="crossref_repair_retry_exhausted",
    ):
        store.list_candidates(
            plan,
            now=NOW + timedelta(days=2),
            policy=policy(
                repair_retry_after_seconds=60,
                max_consecutive_failures=2,
            ),
        )
