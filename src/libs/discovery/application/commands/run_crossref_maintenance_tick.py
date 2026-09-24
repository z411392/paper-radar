from dataclasses import replace
from datetime import datetime

from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_repair import (
    CrossrefMaintenanceResult,
    CrossrefRepairCandidate,
    CrossrefRepairPolicy,
)
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort
from libs.discovery.ports.crossref_repair_store_port import CrossrefRepairStorePort


class RunCrossrefMaintenanceTick:
    """Local maintenance only: finalize safe windows and start bounded repair passes."""

    def __init__(
        self,
        source: CrossrefPageSourcePort,
        store: CrossrefRepairStorePort,
    ) -> None:
        self._source = source
        self._store = store

    def _plan_for(
        self,
        base: CrossrefWindowPlan,
        candidate: CrossrefRepairCandidate,
    ) -> CrossrefWindowPlan:
        definition = replace(
            base.definition,
            from_index=candidate.from_index,
            until_index=candidate.until_index,
        )
        return self._source.compile(definition)

    def __call__(
        self,
        plan: CrossrefWindowPlan,
        *,
        now: datetime,
        policy: CrossrefRepairPolicy,
    ) -> CrossrefMaintenanceResult:
        finalized = []
        for candidate in self._store.list_finalizable(
            plan,
            now=now,
            policy=policy,
        ):
            window_plan = self._plan_for(plan, candidate)
            self._store.finalize_window(
                window_plan,
                candidate.window_id,
                finalized_at=now,
                policy=policy,
            )
            finalized.append(candidate.window_id)

        repairs = []
        for candidate in self._store.list_candidates(
            plan,
            now=now,
            policy=policy,
        ):
            window_plan = self._plan_for(plan, candidate)
            repair = self._store.start_repair(
                window_plan,
                candidate.window_id,
                reason=candidate.reason,
                started_at=now,
            )
            repairs.append(repair.repair_id)
        return CrossrefMaintenanceResult(
            tuple(finalized),
            tuple(repairs),
        )
