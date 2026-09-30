from datetime import datetime, timedelta, timezone

from libs.discovery.adapters.driven.crossref_source_adapter import CrossrefSourceAdapter
from libs.discovery.application.commands.run_crossref_maintenance_tick import (
    RunCrossrefMaintenanceTick,
)
from libs.discovery.dtos.crossref_page import CrossrefWindowInput
from libs.discovery.dtos.crossref_repair import (
    CrossrefRepairCandidate,
    CrossrefRepairPolicy,
    CrossrefRepairRun,
)


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _base():
    source = CrossrefSourceAdapter()
    return source, source.compile(
        CrossrefWindowInput(
            binding_key="personal:3:statistics:1:crossref",
            scope_query="statistics",
            from_index=NOW - timedelta(days=3),
            until_index=NOW - timedelta(days=2),
            contact_email="fixture@example.invalid",
            config_version="v1",
            rows=1000,
        )
    )


class Store:
    def __init__(self):
        self.finalized = []
        self.started = []

    def list_finalizable(self, plan, *, now, policy):
        del plan, now, policy
        return (
            CrossrefRepairCandidate(
                "window:1",
                "finalize",
                NOW - timedelta(days=4),
                NOW - timedelta(days=3),
            ),
            CrossrefRepairCandidate(
                "window:2",
                "finalize",
                NOW - timedelta(days=3),
                NOW - timedelta(days=2),
            ),
        )

    def finalize_window(self, plan, window_id, *, finalized_at, policy):
        self.finalized.append(
            (window_id, plan.definition.from_index, plan.definition.until_index)
        )
        del finalized_at, policy

    def list_candidates(self, plan, *, now, policy):
        del plan, now, policy
        return (
            CrossrefRepairCandidate(
                "window:3",
                "repair_pending",
                NOW - timedelta(days=2),
                NOW - timedelta(days=1),
            ),
        )

    def start_repair(self, plan, window_id, *, reason, started_at):
        self.started.append(
            (
                window_id,
                reason,
                plan.definition.from_index,
                plan.definition.until_index,
            )
        )
        del started_at
        return CrossrefRepairRun(
            "repair:1",
            window_id,
            1,
            "pass:repair",
            2,
            "running",
            reason,
            None,
        )


def test_maintenance_recompiles_each_window_and_performs_no_provider_io() -> None:
    source, plan = _base()
    store = Store()
    result = RunCrossrefMaintenanceTick(source, store)(
        plan,
        now=NOW,
        policy=CrossrefRepairPolicy(3600, 3, 86400, 4),
    )

    assert result.finalized_window_ids == ("window:1", "window:2")
    assert result.started_repair_ids == ("repair:1",)
    assert store.finalized == [
        (
            "window:1",
            NOW - timedelta(days=4),
            NOW - timedelta(days=3),
        ),
        (
            "window:2",
            NOW - timedelta(days=3),
            NOW - timedelta(days=2),
        ),
    ]
    assert store.started == [
        (
            "window:3",
            "repair_pending",
            NOW - timedelta(days=2),
            NOW - timedelta(days=1),
        )
    ]
