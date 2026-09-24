from datetime import datetime
from typing import Protocol

from libs.discovery.dtos.crossref_capture import CrossrefReplayedPage
from libs.discovery.dtos.crossref_harvest import (
    CrossrefHarvestPassState,
    CrossrefHarvestWindowState,
    CrossrefJournalPage,
    CrossrefPendingItem,
)
from libs.discovery.dtos.crossref_page import CrossrefPageRequest, CrossrefWindowPlan


class CrossrefHarvestJournalPort(Protocol):
    def ensure_window(
        self,
        plan: CrossrefWindowPlan,
        created_at: datetime,
    ) -> CrossrefHarvestWindowState: ...

    def start_pass(
        self,
        plan: CrossrefWindowPlan,
        started_at: datetime,
    ) -> CrossrefHarvestPassState: ...

    def read_window(self, window_id: str) -> CrossrefHarvestWindowState: ...

    def read_pass(self, pass_id: str) -> CrossrefHarvestPassState: ...

    def begin_page(
        self,
        pass_id: str,
        request: CrossrefPageRequest,
        created_at: datetime,
    ) -> CrossrefJournalPage: ...

    def record_attempt(
        self,
        page_id: str,
        receipt_id: str,
        *,
        action: str,
        failure_code: str | None,
        recorded_at: datetime,
    ) -> CrossrefJournalPage: ...

    def resume_page(self, pass_id: str) -> CrossrefJournalPage | None: ...

    def save_decoded(
        self,
        page_id: str,
        replayed: CrossrefReplayedPage,
        decoded_at: datetime,
    ) -> CrossrefJournalPage: ...

    def pending_items(self, page_id: str) -> tuple[CrossrefPendingItem, ...]: ...

    def mark_processed(
        self,
        page_id: str,
        ordinal: int,
        *,
        canonical_doi: str,
        outcome_ref: str,
        processed_at: datetime,
    ) -> bool: ...

    def commit_page(
        self,
        pass_id: str,
        page_id: str,
        committed_at: datetime,
    ) -> CrossrefHarvestPassState: ...

    def fail_pass(
        self,
        pass_id: str,
        error_code: str,
        finished_at: datetime,
    ) -> CrossrefHarvestPassState: ...
