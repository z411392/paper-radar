from datetime import datetime

from libs.discovery.dtos.crossref_harvest import CrossrefHarvestStepResult
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.ports.crossref_capture_page_port import CrossrefCapturePagePort
from libs.discovery.ports.crossref_harvest_journal_port import CrossrefHarvestJournalPort
from libs.discovery.ports.crossref_orphan_receipt_recovery_port import (
    CrossrefOrphanReceiptRecoveryPort,
)
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort
from libs.discovery.ports.crossref_replay_capture_port import CrossrefReplayCapturePort


class AdvanceCrossrefHarvestPage:
    """Advance at most one durable page; decoded items remain owned by the projector."""

    def __init__(
        self,
        source: CrossrefPageSourcePort,
        capture: CrossrefCapturePagePort,
        replay: CrossrefReplayCapturePort,
        journal: CrossrefHarvestJournalPort,
        recovery: CrossrefOrphanReceiptRecoveryPort | None = None,
    ) -> None:
        self._source = source
        self._capture = capture
        self._replay = replay
        self._journal = journal
        self._recovery = recovery

    def __call__(
        self,
        plan: CrossrefWindowPlan,
        *,
        now: datetime,
    ) -> CrossrefHarvestStepResult:
        window = self._journal.ensure_window(plan, now)
        current = self._journal.start_pass(plan, now)
        if current.state == "completed":
            return CrossrefHarvestStepResult(
                "pass_completed",
                window.window_id,
                current.pass_id,
                None,
                None,
                0,
                None,
            )
        if current.current_cursor is None:
            raise CrossrefProtocolError("crossref_cursor_missing")
        request = self._source.page(plan, current.current_cursor)
        page = self._journal.begin_page(current.pass_id, request, now)

        if page.state == "requested" and self._recovery is not None:
            recovered = self._recovery.find(
                plan,
                page.page_id,
                request,
            )
            if recovered is not None:
                page = self._journal.record_attempt(
                    page.page_id,
                    recovered.receipt_id,
                    action=recovered.action,
                    failure_code=recovered.failure_code,
                    recorded_at=now,
                )
                if recovered.action != "accept":
                    return CrossrefHarvestStepResult(
                        recovered.action,
                        window.window_id,
                        current.pass_id,
                        page.page_id,
                        recovered.receipt_id,
                        0,
                        recovered.failure_code,
                    )

        if page.state == "requested":
            try:
                captured = self._capture(
                    plan,
                    request,
                    attempt_key=page.page_id,
                )
            except CrossrefCaptureError as exc:
                if exc.receipt_id is not None:
                    self._journal.record_attempt(
                        page.page_id,
                        exc.receipt_id,
                        action="stop",
                        failure_code=exc.code,
                        recorded_at=now,
                    )
                return CrossrefHarvestStepResult(
                    "stopped",
                    window.window_id,
                    current.pass_id,
                    page.page_id,
                    exc.receipt_id,
                    0,
                    exc.code,
                )
            page = self._journal.record_attempt(
                page.page_id,
                captured.receipt_id,
                action=captured.decision.action,
                failure_code=captured.decision.failure_code,
                recorded_at=now,
            )
            if captured.decision.action != "accept":
                return CrossrefHarvestStepResult(
                    captured.decision.action,
                    window.window_id,
                    current.pass_id,
                    page.page_id,
                    captured.receipt_id,
                    0,
                    captured.decision.failure_code,
                )

        if page.state == "captured":
            assert page.successful_receipt_id is not None
            try:
                replayed = self._replay(
                    plan,
                    request,
                    page.successful_receipt_id,
                )
            except (CrossrefCaptureError, CrossrefProtocolError) as exc:
                code = getattr(exc, "code", "crossref_replay_failed")
                self._journal.note_page_error(page.page_id, code)
                return CrossrefHarvestStepResult(
                    "replay_failed",
                    window.window_id,
                    current.pass_id,
                    page.page_id,
                    page.successful_receipt_id,
                    0,
                    code,
                )
            page = self._journal.save_decoded(page.page_id, replayed, now)

        pending = self._journal.pending_items(page.page_id)
        if pending:
            return CrossrefHarvestStepResult(
                "projection_required",
                window.window_id,
                current.pass_id,
                page.page_id,
                page.successful_receipt_id,
                len(pending),
                None,
            )

        if page.state == "decoded":
            current = self._journal.commit_page(
                current.pass_id,
                page.page_id,
                now,
            )
        return CrossrefHarvestStepResult(
            "pass_completed" if current.state == "completed" else "page_committed",
            window.window_id,
            current.pass_id,
            page.page_id,
            page.successful_receipt_id,
            0,
            None,
        )
