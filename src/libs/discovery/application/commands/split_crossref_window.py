from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from libs.discovery.domain.services.crossref_window_split_rules import CrossrefWindowSplitRules as Rules
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.dtos.crossref_window_split import CrossrefWindowSplit
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.exceptions.crossref_window_split_error import CrossrefWindowSplitError
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort
from libs.discovery.ports.crossref_window_split_store_port import CrossrefWindowSplitStorePort

if TYPE_CHECKING:
    from libs.discovery.ports.crossref_harvest_journal_port import CrossrefHarvestJournalPort


class SplitCrossrefWindow:
    """Compile outside SQL; the store atomically validates and creates both children."""

    def __init__(self, source: CrossrefPageSourcePort, journal: CrossrefHarvestJournalPort,
                 store: CrossrefWindowSplitStorePort) -> None:
        self._source, self._journal, self._store = source, journal, store

    def __call__(self, plan: CrossrefWindowPlan, *, split_at: datetime,
                 reason: str, created_at: datetime) -> CrossrefWindowSplit:
        if not isinstance(plan, CrossrefWindowPlan):
            raise CrossrefWindowSplitError("invalid_crossref_split_plan")
        try:
            canonical = self._source.compile(plan.definition)
            if canonical != plan:
                raise CrossrefWindowSplitError("invalid_crossref_split_plan")
            boundary = Rules.boundary(canonical, split_at)
            reason = Rules.reason(reason)
            created = Rules.instant(created_at)
            left = self._source.compile(replace(canonical.definition, until_index=boundary))
            right = self._source.compile(replace(canonical.definition, from_index=boundary))
        except CrossrefProtocolError as exc:
            raise CrossrefWindowSplitError("invalid_crossref_split_plan") from exc
        # This is a read only. The store rechecks the parent under its writer lock.
        self._journal.read_window(Rules.window_id(canonical))
        return self._store.split(canonical, left, right, reason=reason, created_at=created)
