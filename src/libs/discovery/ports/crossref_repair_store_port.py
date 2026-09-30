from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_repair import (
    CrossrefBindingWatermark,
    CrossrefRepairCandidate,
    CrossrefRepairPolicy,
    CrossrefRepairRun,
    CrossrefWindowFinalization,
)


class CrossrefRepairStorePort(Protocol):
    def ensure_stream(
        self,
        plan: CrossrefWindowPlan,
        created_at: datetime,
    ) -> str: ...

    def list_finalizable(
        self,
        plan: CrossrefWindowPlan,
        *,
        now: datetime,
        policy: CrossrefRepairPolicy,
    ) -> tuple[CrossrefRepairCandidate, ...]: ...

    def list_candidates(
        self,
        plan: CrossrefWindowPlan,
        *,
        now: datetime,
        policy: CrossrefRepairPolicy,
    ) -> tuple[CrossrefRepairCandidate, ...]: ...

    def start_repair(
        self,
        plan: CrossrefWindowPlan,
        window_id: str,
        *,
        reason: str,
        started_at: datetime,
    ) -> CrossrefRepairRun: ...

    def reconcile_repair(
        self,
        repair_id: str,
        finished_at: datetime,
    ) -> CrossrefRepairRun: ...

    def finalize_window(
        self,
        plan: CrossrefWindowPlan,
        window_id: str,
        *,
        finalized_at: datetime,
        policy: CrossrefRepairPolicy,
    ) -> CrossrefWindowFinalization: ...

    def read_watermark(
        self,
        plan: CrossrefWindowPlan,
    ) -> CrossrefBindingWatermark | None: ...
