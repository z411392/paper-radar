"""Author-owned split/leaf regressions. Real SQLite, no provider or kernel I/O."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.adapters.driven.sqlite_crossref_repair_store_adapter import (
    SqliteCrossrefRepairStoreAdapter,
)
from libs.discovery.adapters.driven.sqlite_crossref_window_split_store_adapter import (
    SqliteCrossrefWindowSplitStoreAdapter,
)
from libs.discovery.application.commands.split_crossref_window import SplitCrossrefWindow
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.dtos.crossref_repair import CrossrefRepairPolicy
from libs.discovery.exceptions.crossref_repair_error import CrossrefRepairError
from libs.discovery.exceptions.crossref_window_split_error import CrossrefWindowSplitError

NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
START = NOW - timedelta(days=3)
END = START + timedelta(days=1)
MID = START + timedelta(hours=12)
ROOT = Path(__file__).resolve().parents[5]


def identity(plan):
    content = json.dumps((plan.definition.binding_key, plan.query_fingerprint),
                         ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return "crossref-window:" + hashlib.sha256(content).hexdigest()


def plan(start=START, end=END, **changes):
    definition = CrossrefWindowInput("binding:test", "statistics", start, end,
                                     "fixture@example.invalid", "v1", 2)
    return CrossrefSourceAdapter().compile(replace(definition, **changes))


def connect(path):
    connection = sqlite3.connect(path, isolation_level=None, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def seed(path, value, state="pending"):
    key = identity(value)
    d = value.definition
    with connect(path) as c:
        c.execute("INSERT OR IGNORE INTO crossref_harvest_windows VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (key, d.binding_key, value.query_fingerprint, d.config_version,
                   d.from_index.isoformat(), d.until_index.isoformat(), d.rows, state,
                   NOW.isoformat(), NOW.isoformat()))
    return key


def complete(path, value):
    key = seed(path, value)
    with connect(path) as c:
        c.execute("UPDATE crossref_harvest_windows SET state='traversed' WHERE id=?", (key,))
        c.execute("INSERT INTO crossref_harvest_passes("
                  "id,window_id,pass_no,parameters_fingerprint,state,current_cursor,"
                  "traversal_complete,accounting_complete,source_completeness,started_at,finished_at) "
                  "VALUES(?,?,1,?,'completed',NULL,1,1,'provisional',?,?)",
                  ("pass:" + key, key, value.parameters_fingerprint, NOW.isoformat(), NOW.isoformat()))
    return key


class Reader:
    """Only journal lookup is needed; the split store owns every write."""
    def __init__(self, path):
        self.path = path

    def read_window(self, key):
        with connect(self.path) as c:
            row = c.execute("SELECT * FROM crossref_harvest_windows WHERE id=?", (key,)).fetchone()
        if row is None:
            raise RuntimeError("window missing")
        return row

    def ensure_window(self, *args):
        pytest.fail("child creation must not escape the split transaction")


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "split.sqlite3"
    c = connect(path)
    for name in ("0001-object-registry.sql", "0011-crossref-harvest.sql",
                 "0012-crossref-repair.sql", "0013-crossref-window-splits.sql"):
        c.executescript((ROOT / "migrations" / name).read_text())
    c.close()
    source = CrossrefSourceAdapter()
    store = SqliteCrossrefWindowSplitStoreAdapter(lambda: connect(path))
    command = SplitCrossrefWindow(source, Reader(path), store)
    repair = SqliteCrossrefRepairStoreAdapter(lambda: connect(path))
    seed(path, plan())
    return path, store, command, repair


def count(path, table):
    assert table in {"crossref_harvest_windows", "crossref_window_splits"}
    with connect(path) as c:
        return c.execute("SELECT count(*) FROM " + table).fetchone()[0]


def split(command, value=None, **changes):
    args = dict(split_at=MID, reason="volume_budget", created_at=NOW)
    args.update(changes)
    return command(value or plan(), **args)


def test_exact_boundary_and_replay_leave_history_unchanged(env):
    path, _, command, _ = env
    first = split(command)
    replay = split(command, created_at=NOW + timedelta(hours=1))
    assert (first.left_from, first.left_until, first.right_from, first.right_until) == (START, MID, MID, END)
    assert (replay.left_window_id, replay.right_window_id) == (first.left_window_id, first.right_window_id)
    assert first.replayed is False and replay.replayed is True
    assert count(path, "crossref_harvest_windows") == 3
    assert count(path, "crossref_window_splits") == 1
    with connect(path) as c:
        row = c.execute("SELECT * FROM crossref_window_splits").fetchone()
        assert row["created_at"] == NOW.isoformat()
        assert c.execute("SELECT state FROM crossref_harvest_windows WHERE id=?",
                         (first.parent_window_id,)).fetchone()[0] == "pending"


@pytest.mark.parametrize("bad", [START, END, START-timedelta(seconds=1), END+timedelta(seconds=1),
                                MID.replace(tzinfo=None), MID.replace(microsecond=1), "2026-09-21", None])
def test_invalid_split_point_creates_nothing(env, bad):
    path, _, command, _ = env
    with pytest.raises(CrossrefWindowSplitError):
        split(command, split_at=bad)
    assert count(path, "crossref_harvest_windows") == 1
    assert count(path, "crossref_window_splits") == 0


@pytest.mark.parametrize("reason", ["", "unknown", None, ["operator"], True])
def test_invalid_reason_creates_nothing(env, reason):
    path, _, command, _ = env
    with pytest.raises(CrossrefWindowSplitError):
        split(command, reason=reason)
    assert count(path, "crossref_harvest_windows") == 1


def test_timezone_equivalent_point_is_exact_replay(env):
    _, _, command, _ = env
    first = split(command)
    again = split(command, split_at=MID.astimezone(timezone(timedelta(hours=8))))
    assert again.left_window_id == first.left_window_id and again.replayed


@pytest.mark.parametrize("kind", ["state_only", "running_pass", "running_repair"])
def test_any_inflight_parent_rejects_without_children(env, kind):
    path, _, command, _ = env
    key = identity(plan())
    with connect(path) as c:
        if kind == "state_only":
            c.execute("UPDATE crossref_harvest_windows SET state='running' WHERE id=?", (key,))
        else:
            c.execute("INSERT INTO crossref_harvest_passes("
                      "id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
                      "VALUES('active',?,1,?,'running','*',?)",
                      (key, plan().parameters_fingerprint, NOW.isoformat()))
            if kind == "running_repair":
                c.execute("UPDATE crossref_harvest_passes SET state='failed',error_code='timeout' "
                          "WHERE id='active'")
                c.execute("INSERT INTO crossref_repair_runs "
                          "VALUES('repair',?,1,'active','operator','running',?,NULL,NULL)",
                          (key, NOW.isoformat()))
    with pytest.raises(CrossrefWindowSplitError, match="crossref_split_parent_running"):
        split(command)
    assert count(path, "crossref_harvest_windows") == 1


def test_finalized_parent_rejects_without_children(env):
    path, _, command, repair = env
    key = complete(path, plan())
    repair.finalize_window(plan(), key, finalized_at=NOW, policy=CrossrefRepairPolicy(0, 3, 86400, 4))
    with pytest.raises(CrossrefWindowSplitError, match="crossref_split_parent_finalized"):
        split(command)
    assert count(path, "crossref_harvest_windows") == 1


def test_atomic_rollback_on_right_child_failure(env):
    path, _, command, _ = env
    right_id = identity(plan(MID, END))
    with connect(path) as c:
        c.execute("CREATE TRIGGER fail_right BEFORE INSERT ON crossref_harvest_windows "
                  "WHEN NEW.id='" + right_id + "' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(CrossrefWindowSplitError):
        split(command)
    assert count(path, "crossref_harvest_windows") == 1
    assert count(path, "crossref_window_splits") == 0


def test_atomic_rollback_on_relation_failure(env):
    path, _, command, _ = env
    with connect(path) as c:
        c.execute("CREATE TRIGGER fail_relation BEFORE INSERT ON crossref_window_splits "
                  "BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(CrossrefWindowSplitError):
        split(command)
    assert count(path, "crossref_harvest_windows") == 1


@pytest.mark.parametrize("change", [{"split_at": MID+timedelta(hours=1)}, {"reason": "operator"}])
def test_conflicting_replay_never_replaces_children(env, change):
    path, _, command, _ = env
    split(command)
    with pytest.raises(CrossrefWindowSplitError, match="crossref_split_conflict"):
        split(command, **change)
    assert count(path, "crossref_harvest_windows") == 3


def test_concurrent_split_is_one_atomic_topology(env):
    path, _, command, _ = env
    barrier = Barrier(2)
    def task():
        barrier.wait(timeout=3)
        return split(command)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: task(), range(2)))
    assert sorted(x.replayed for x in results) == [False, True]
    assert count(path, "crossref_harvest_windows") == 3


def test_nested_children_are_the_only_watermark_segments(env):
    path, _, command, repair = env
    first = split(command)
    quarter = START + timedelta(hours=6)
    second = split(command, plan(START, MID), split_at=quarter)
    policy = CrossrefRepairPolicy(0, 3, 86400, 4)
    parts = (plan(START, quarter), plan(quarter, MID), plan(MID, END))
    for value in parts:
        complete(path, value)
    for value in reversed(parts[1:]):
        repair.finalize_window(value, identity(value), finalized_at=NOW, policy=policy)
        assert repair.read_watermark(plan()) is None
    repair.finalize_window(parts[0], identity(parts[0]), finalized_at=NOW, policy=policy)
    assert repair.read_watermark(plan()).finalized_until == END
    with connect(path) as c:
        assert c.execute("SELECT count(*) FROM crossref_window_finalizations").fetchone()[0] == 3
    assert first.parent_window_id != second.parent_window_id


def test_split_parent_is_never_a_repair_or_finalization_candidate(env):
    path, _, command, repair = env
    complete(path, plan())
    parent = split(command).parent_window_id
    policy = CrossrefRepairPolicy(0, 3, 86400, 4)
    assert all(x.window_id != parent for x in repair.list_finalizable(plan(), now=NOW, policy=policy))
    with connect(path) as c:
        c.execute("UPDATE crossref_harvest_windows SET state='repair_pending' WHERE id=?", (parent,))
    assert repair.list_candidates(plan(), now=NOW, policy=policy) == ()
    with pytest.raises(CrossrefRepairError, match="crossref_window_superseded"):
        repair.finalize_window(plan(), parent, finalized_at=NOW, policy=policy)
    with pytest.raises(CrossrefRepairError, match="crossref_window_superseded"):
        repair.start_repair(plan(), parent, reason="operator", started_at=NOW)


def test_database_blocks_parent_restart_and_topology_deletion(env):
    path, _, command, _ = env
    parent = split(command).parent_window_id
    with connect(path) as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("INSERT INTO crossref_harvest_passes("
                      "id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
                      "VALUES('stale-owner',?,1,?,'running','*',?)",
                      (parent, plan().parameters_fingerprint, NOW.isoformat()))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM crossref_window_splits WHERE parent_window_id=?", (parent,))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE crossref_window_splits SET reason='operator' WHERE parent_window_id=?",
                      (parent,))


def test_preexisting_children_reused_only_with_exact_definition(env):
    path, _, command, _ = env
    left = seed(path, plan(START, MID))
    result = split(command)
    assert result.left_window_id == left
    assert count(path, "crossref_harvest_windows") == 3


def test_corrupt_child_definition_cannot_be_adopted(env):
    path, _, command, _ = env
    left = seed(path, plan(START, MID))
    with connect(path) as c:
        c.execute("UPDATE crossref_harvest_windows SET config_version='bad' WHERE id=?", (left,))
    with pytest.raises(CrossrefWindowSplitError, match="crossref_split_child_conflict"):
        split(command)
    assert count(path, "crossref_harvest_windows") == 2


def test_finalizable_limit_is_enforced(env):
    path, _, _, repair = env
    for offset in range(4):
        complete(path, plan(START+timedelta(days=offset), END+timedelta(days=offset)))
    assert len(repair.list_finalizable(plan(), now=NOW+timedelta(days=2),
                                      policy=CrossrefRepairPolicy(0, 3, 86400, 2))) == 2


@pytest.mark.parametrize("change", [{"query_fingerprint": "0"*64}, {"parameters_fingerprint": "1"*64},
                                   {"parameters": (("rows", "999"),)}])
def test_forged_plan_never_mutates_topology(env, change):
    path, _, command, _ = env
    with pytest.raises(CrossrefWindowSplitError, match="invalid_crossref_split_plan"):
        split(command, replace(plan(), **change))
    assert count(path, "crossref_harvest_windows") == 1


def test_store_revalidates_child_scope_even_when_called_directly(env):
    path, store, _, _ = env
    with pytest.raises(CrossrefWindowSplitError, match="invalid_crossref_split_children"):
        store.split(plan(), plan(START, MID), plan(MID, END, scope_query="another topic"),
                    reason="operator", created_at=NOW)
    assert count(path, "crossref_harvest_windows") == 1


def test_active_preexisting_child_cannot_be_reparented(env):
    path, _, command, _ = env
    seed(path, plan(START, MID), state="running")
    with pytest.raises(CrossrefWindowSplitError, match="crossref_split_child_in_use"):
        split(command)
    assert count(path, "crossref_harvest_windows") == 2


def test_conflicting_concurrent_midpoints_have_only_one_winner(env):
    path, _, command, _ = env
    barrier = Barrier(2)
    def task(midpoint):
        barrier.wait(timeout=3)
        try:
            return split(command, split_at=midpoint)
        except CrossrefWindowSplitError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(task, (MID, MID+timedelta(hours=1))))
    assert sum(isinstance(result, str) for result in results) == 1
    assert "crossref_split_conflict" in results
    assert count(path, "crossref_harvest_windows") == 3


def test_finalization_racing_split_is_serialized(env):
    path, _, command, repair = env
    key = complete(path, plan())
    barrier = Barrier(2)
    def finalize():
        barrier.wait(timeout=3)
        try:
            repair.finalize_window(plan(), key, finalized_at=NOW, policy=CrossrefRepairPolicy(0, 3, 86400, 4))
            return "finalized"
        except CrossrefRepairError as exc:
            return exc.code
    def do_split():
        barrier.wait(timeout=3)
        try:
            split(command)
            return "split"
        except CrossrefWindowSplitError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(finalize), pool.submit(do_split)
        results = (a.result(), b.result())
    assert results in {("finalized", "crossref_split_parent_finalized"),
                       ("crossref_window_superseded", "split")}
    assert count(path, "crossref_harvest_windows") == (3 if "split" in results else 1)


def test_stale_failed_pass_cannot_be_revived_after_split(env):
    path, _, command, _ = env
    key = identity(plan())
    with connect(path) as c:
        c.execute("INSERT INTO crossref_harvest_passes("
                  "id,window_id,pass_no,parameters_fingerprint,state,current_cursor,started_at) "
                  "VALUES('old',?,1,?,'failed','*',?)", (key, plan().parameters_fingerprint, NOW.isoformat()))
    split(command)
    with connect(path) as c:
        for state in ("running", "completed"):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute("UPDATE crossref_harvest_passes SET state=? WHERE id='old'", (state,))


def test_topology_member_bounds_cannot_change(env):
    path, _, command, _ = env
    result = split(command)
    with connect(path) as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE crossref_harvest_windows SET until_index=? WHERE id=?",
                      ((MID+timedelta(seconds=1)).isoformat(), result.left_window_id))


def test_depth_limit_rolls_back_new_children(env, monkeypatch):
    from libs.discovery.domain.services.crossref_window_split_rules import CrossrefWindowSplitRules
    path, _, command, _ = env
    split(command)
    monkeypatch.setattr(CrossrefWindowSplitRules, "MAX_DEPTH", 1)
    with pytest.raises(CrossrefWindowSplitError, match="crossref_split_depth_limit"):
        split(command, plan(START, MID), split_at=START+timedelta(hours=6))
    assert count(path, "crossref_harvest_windows") == 3


def test_failing_scope_guard_does_not_advance_cached_watermark(env):
    path, _, command, repair = env
    result = split(command)
    left, right = plan(START, MID), plan(MID, END)
    complete(path, left)
    policy = CrossrefRepairPolicy(0, 3, 86400, 4)
    repair.finalize_window(left, result.left_window_id, finalized_at=NOW, policy=policy)
    before = repair.read_watermark(plan())
    assert before.finalized_until == MID
    with pytest.raises(CrossrefRepairError):
        repair.finalize_window(right, result.right_window_id, finalized_at=NOW, policy=policy)
    assert repair.read_watermark(plan()) == before


def test_no_schema_installation_is_invented_by_split(env):
    path, store, _, _ = env
    other = path.parent / "old.sqlite3"
    with connect(other) as c:
        for name in ("0001-object-registry.sql", "0011-crossref-harvest.sql", "0012-crossref-repair.sql"):
            c.executescript((ROOT / "migrations" / name).read_text())
    seed(other, plan())
    store = SqliteCrossrefWindowSplitStoreAdapter(lambda: connect(other))
    with pytest.raises(CrossrefWindowSplitError, match="crossref_split_database_error"):
        store.split(plan(), plan(START, MID), plan(MID, END), reason="operator", created_at=NOW)
    assert count(other, "crossref_harvest_windows") == 1


def test_foreign_keys_off_is_rejected_before_writes(env):
    path, _, _, _ = env
    def unsafe():
        return sqlite3.connect(path, isolation_level=None)
    store = SqliteCrossrefWindowSplitStoreAdapter(unsafe)
    with pytest.raises(CrossrefWindowSplitError, match="foreign_keys_required"):
        store.split(plan(), plan(START, MID), plan(MID, END), reason="operator", created_at=NOW)
    assert count(path, "crossref_harvest_windows") == 1
