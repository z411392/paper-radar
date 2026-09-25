from collections.abc import Callable
from datetime import datetime, timezone

from libs.discovery.dtos.crossref_harvest import CrossrefHarvestStepResult
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.ports.crossref_harvest_journal_port import CrossrefHarvestJournalPort
from libs.research_workflow.ports.run_crossref_harvest_window_port import (
    RunCrossrefHarvestWindowPort,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)
from libs.scholarly_catalog.ports.project_crossref_pending_item_port import (
    ProjectCrossrefPendingItemPort,
)


_QUARANTINE_CODES = frozenset({
    "crossref_provider_doi_invalid",
    "crossref_provider_item_mismatch",
    "invalid_crossref_provider_revision",
})


class RunProjectedCrossrefHarvestWindow:
    """Compose discovery traversal with catalog projection without crossing owners."""

    def __init__(
        self,
        harvest: RunCrossrefHarvestWindowPort,
        journal: CrossrefHarvestJournalPort,
        project: ProjectCrossrefPendingItemPort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._harvest = harvest
        self._journal = journal
        self._project = project
        self._clock = clock

    def _project_page(
        self,
        result: CrossrefHarvestStepResult,
    ) -> CrossrefHarvestStepResult | None:
        page_id = result.page_id
        if page_id is None:
            return CrossrefHarvestStepResult(
                "projection_failed",
                result.window_id,
                result.pass_id,
                None,
                result.receipt_id,
                0,
                "crossref_projection_page_missing",
            )
        pending = self._journal.pending_items(page_id)
        if not pending:
            return None
        for item in pending:
            try:
                projected = self._project(
                    item,
                    observed_at=self._clock(),
                )
            except CrossrefProviderProjectionError as exc:
                if exc.code in _QUARANTINE_CODES:
                    self._journal.mark_projection_quarantined(
                        page_id,
                        item.ordinal,
                        error_code=exc.code,
                        quarantined_at=self._clock(),
                    )
                    continue
                self._journal.note_page_error(page_id, exc.code)
                return CrossrefHarvestStepResult(
                    "projection_failed",
                    result.window_id,
                    result.pass_id,
                    page_id,
                    result.receipt_id,
                    len(self._journal.pending_items(page_id)),
                    exc.code,
                )
            self._journal.mark_processed(
                page_id,
                item.ordinal,
                canonical_doi=projected.canonical_doi,
                outcome_ref=projected.provider_revision_id,
                processed_at=self._clock(),
            )
        return None

    def __call__(
        self,
        plan: CrossrefWindowPlan,
        *,
        owner_id: str,
        max_pages: int,
        lease_seconds: int,
    ) -> CrossrefHarvestStepResult:
        if type(max_pages) is not int or not 1 <= max_pages <= 1000:
            return CrossrefHarvestStepResult(
                "projection_failed",
                "",
                "",
                None,
                None,
                0,
                "invalid_crossref_run_budget",
            )

        touched_pages: set[str] = set()
        operation_budget = max_pages * 3 + 3
        last: CrossrefHarvestStepResult | None = None
        for _ in range(operation_budget):
            result = self._harvest(
                plan,
                owner_id=owner_id,
                max_pages=1,
                lease_seconds=lease_seconds,
            )
            last = result
            if result.page_id is not None:
                touched_pages.add(result.page_id)
            if result.state == "projection_required":
                if result.page_id is None:
                    return CrossrefHarvestStepResult(
                        "projection_failed",
                        result.window_id,
                        result.pass_id,
                        None,
                        result.receipt_id,
                        0,
                        "crossref_projection_page_missing",
                    )
                if not self._journal.pending_items(result.page_id):
                    return CrossrefHarvestStepResult(
                        "projection_failed",
                        result.window_id,
                        result.pass_id,
                        result.page_id,
                        result.receipt_id,
                        0,
                        "crossref_projection_no_progress",
                    )
                failed = self._project_page(result)
                if failed is not None:
                    return failed
                # Projection changes owner-local item outcomes only. The discovery
                # journal still owns the actual page commit, so always re-enter the
                # bounded harvest runner once to commit this same decoded page.
                continue
            if result.state == "page_committed":
                if len(touched_pages) >= max_pages:
                    return result
                continue
            return result

        if last is None:
            return CrossrefHarvestStepResult(
                "projection_failed",
                "",
                "",
                None,
                None,
                0,
                "crossref_projection_no_progress",
            )
        return CrossrefHarvestStepResult(
            "page_committed",
            last.window_id,
            last.pass_id,
            last.page_id,
            last.receipt_id,
            0,
            "crossref_projection_operation_budget",
        )
