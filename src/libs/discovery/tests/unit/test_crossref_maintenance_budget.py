from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.application.commands.run_crossref_maintenance_tick import RunCrossrefMaintenanceTick
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.dtos.crossref_repair import CrossrefRepairCandidate, CrossrefRepairPolicy
from libs.discovery.exceptions.crossref_repair_error import CrossrefRepairError

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


class Store:
    def __init__(self, final_count):
        self.final_count = final_count
        self.writes = []
        self.repair_limit = None

    def list_finalizable(self, plan, *, now, policy):
        return tuple(CrossrefRepairCandidate(str(i), "finalize", NOW-timedelta(days=2),
                                             NOW-timedelta(days=1)) for i in range(self.final_count))

    def finalize_window(self, plan, window_id, *, finalized_at, policy):
        self.writes.append("finalize")

    def list_candidates(self, plan, *, now, policy):
        self.repair_limit = policy.max_windows
        return tuple(CrossrefRepairCandidate(str(i), "repair_pending", NOW-timedelta(days=2),
                                             NOW-timedelta(days=1)) for i in range(10))

    def start_repair(self, plan, window_id, *, reason, started_at):
        self.writes.append("repair")
        return SimpleNamespace(repair_id="repair:"+window_id)


@pytest.mark.parametrize("final_count", [0, 1, 2, 10])
def test_one_budget_is_shared_by_finalization_and_repair(final_count):
    source = CrossrefSourceAdapter()
    plan = source.compile(CrossrefWindowInput("binding", "stats", NOW-timedelta(days=2),
                                              NOW-timedelta(days=1), "fixture@example.invalid", "v1", 2))
    store = Store(final_count)
    result = RunCrossrefMaintenanceTick(source, store)(plan, now=NOW,
                                                      policy=CrossrefRepairPolicy(0, 3, 86400, 2))
    assert len(result.finalized_window_ids) + len(result.started_repair_ids) == 2
    assert len(store.writes) == 2
    if final_count < 2:
        assert store.repair_limit == 2-final_count
    else:
        assert store.repair_limit is None


def test_invalid_budget_fails_before_store_reads():
    class Forbidden:
        def __getattr__(self, name):
            pytest.fail("invalid policy must not touch store")
    with pytest.raises(CrossrefRepairError, match="invalid_crossref_repair_policy"):
        RunCrossrefMaintenanceTick(CrossrefSourceAdapter(), Forbidden())(
            None, now=NOW, policy=CrossrefRepairPolicy(0, 3, 86400, True))
