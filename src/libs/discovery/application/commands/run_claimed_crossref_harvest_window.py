"""Bounded claimed Crossref traversal; decoded catalog projection is deliberately external."""
from collections.abc import Callable
from datetime import datetime, timezone

from libs.discovery.dtos.crossref_harvest import CrossrefHarvestStepResult
from libs.discovery.dtos.crossref_page import CrossrefWindowPlan
from libs.discovery.exceptions.crossref_attachment_error import CrossrefAttachmentError
from libs.discovery.exceptions.crossref_capture_claim_error import CrossrefCaptureClaimError
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_capture_inbox_error import CrossrefCaptureInboxError
from libs.discovery.exceptions.crossref_harvest_journal_error import CrossrefHarvestJournalError
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
from libs.discovery.ports.attach_claimed_crossref_capture_port import (
    AttachClaimedCrossrefCapturePort,
)
from libs.discovery.ports.capture_claimed_crossref_response_port import (
    CaptureClaimedCrossrefResponsePort,
)
from libs.discovery.ports.claimed_crossref_attachment_port import (
    ClaimedCrossrefAttachmentPort,
)
from libs.discovery.ports.crossref_capture_claim_store_port import (
    CrossrefCaptureClaimStorePort,
)
from libs.discovery.ports.crossref_capture_inbox_port import CrossrefCaptureInboxPort
from libs.discovery.ports.crossref_harvest_journal_port import CrossrefHarvestJournalPort
from libs.discovery.ports.crossref_page_source_port import CrossrefPageSourcePort
from libs.discovery.ports.crossref_replay_capture_port import CrossrefReplayCapturePort
from libs.discovery.ports.resolve_claimed_crossref_capture_port import (
    ResolveClaimedCrossrefCapturePort,
)
from libs.kernel.exceptions.storage_error import StorageError
from libs.kernel.ports.read_workspace_info_port import ReadWorkspaceInfoPort


_RETRY_CLAIM_CODES = frozenset({
    "crossref_claim_busy",
    "crossref_claim_retry_not_due",
    "crossref_claim_database_busy",
    "crossref_claim_expired",
})
_RETRY_RATE_CODES = frozenset({
    "crossref_provider_busy",
    "crossref_provider_deferred",
})


class RunClaimedCrossrefHarvestWindow:
    def __init__(
        self,
        source: CrossrefPageSourcePort,
        journal: CrossrefHarvestJournalPort,
        claims: CrossrefCaptureClaimStorePort,
        inbox: CrossrefCaptureInboxPort,
        capture: CaptureClaimedCrossrefResponsePort,
        attachments: ClaimedCrossrefAttachmentPort,
        recover_attachment: AttachClaimedCrossrefCapturePort,
        resolve: ResolveClaimedCrossrefCapturePort,
        replay: CrossrefReplayCapturePort,
        workspace: ReadWorkspaceInfoPort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._source = source
        self._journal = journal
        self._claims = claims
        self._inbox = inbox
        self._capture = capture
        self._attachments = attachments
        self._recover_attachment = recover_attachment
        self._resolve = resolve
        self._replay = replay
        self._workspace = workspace
        self._clock = clock

    @staticmethod
    def _result(
        state: str,
        window_id: str,
        pass_id: str,
        page_id: str | None,
        receipt_id: str | None,
        pending: int = 0,
        error: str | None = None,
    ) -> CrossrefHarvestStepResult:
        return CrossrefHarvestStepResult(
            state,
            window_id,
            pass_id,
            page_id,
            receipt_id,
            pending,
            error,
        )

    def _claim_error(
        self,
        exc: CrossrefCaptureClaimError,
        window_id: str,
        pass_id: str,
        page_id: str,
    ) -> CrossrefHarvestStepResult:
        state = "retry" if exc.code in _RETRY_CLAIM_CODES else "stop"
        return self._result(
            state,
            window_id,
            pass_id,
            page_id,
            None,
            error=exc.code,
        )

    def __call__(
        self,
        plan: CrossrefWindowPlan,
        *,
        owner_id: str,
        max_pages: int,
        lease_seconds: int,
    ) -> CrossrefHarvestStepResult:
        if (
            not isinstance(owner_id, str)
            or not owner_id.strip()
            or type(max_pages) is not int
            or not 1 <= max_pages <= 1000
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 86400
        ):
            raise CrossrefHarvestJournalError("invalid_crossref_run_budget")

        started = self._clock()
        window = self._journal.ensure_window(plan, started)
        current = self._journal.start_pass(plan, started)
        last_page_id = None
        last_receipt = None

        for _ in range(max_pages):
            if current.state == "completed":
                return self._result(
                    "pass_completed",
                    window.window_id,
                    current.pass_id,
                    last_page_id,
                    last_receipt,
                )
            if current.current_cursor is None:
                return self._result(
                    "replay_failed",
                    window.window_id,
                    current.pass_id,
                    last_page_id,
                    last_receipt,
                    error="crossref_cursor_missing",
                )
            try:
                request = self._source.page(plan, current.current_cursor)
                page = self._journal.begin_page(
                    current.pass_id,
                    request,
                    self._clock(),
                )
                last_page_id = page.page_id

                if page.state == "decoded":
                    pending = self._journal.pending_items(page.page_id)
                    if pending:
                        return self._result(
                            "projection_required",
                            window.window_id,
                            current.pass_id,
                            page.page_id,
                            page.successful_receipt_id,
                            len(pending),
                        )
                    current = self._journal.commit_page(
                        current.pass_id,
                        page.page_id,
                        self._clock(),
                    )
                    continue

                receipt_id = page.successful_receipt_id
                if page.state == "requested":
                    latest = self._claims.latest(page.page_id)
                    if latest is not None and latest.state == "dispatching":
                        if self._inbox.load(latest) is None:
                            return self._result(
                                "stop",
                                window.window_id,
                                current.pass_id,
                                page.page_id,
                                None,
                                error="crossref_claim_outcome_unknown",
                            )
                        attached = self._recover_attachment(plan, latest)
                        resolution = self._resolve(plan, latest)
                        if resolution.action == "stop":
                            return self._result(
                                "stop",
                                window.window_id,
                                current.pass_id,
                                page.page_id,
                                attached.receipt_id,
                                error=resolution.failure_code,
                            )
                        if resolution.action == "retry":
                            due = resolution.retry_not_before
                            if due is None or self._clock() < due:
                                return self._result(
                                    "retry",
                                    window.window_id,
                                    current.pass_id,
                                    page.page_id,
                                    attached.receipt_id,
                                    error=resolution.failure_code,
                                )
                        else:
                            receipt_id = attached.receipt_id

                    if receipt_id is None:
                        info = self._workspace()
                        if not info.external_effects_enabled:
                            return self._result(
                                "stop",
                                window.window_id,
                                current.pass_id,
                                page.page_id,
                                None,
                                error="crossref_claim_effects_disabled",
                            )
                        try:
                            claim = self._claims.reserve(
                                plan,
                                current.pass_id,
                                request,
                                owner_id=owner_id,
                                expected_workspace_id=info.workspace_id,
                                expected_epoch=info.epoch,
                                now=self._clock(),
                                lease_seconds=lease_seconds,
                            )
                        except CrossrefCaptureClaimError as exc:
                            return self._claim_error(
                                exc,
                                window.window_id,
                                current.pass_id,
                                page.page_id,
                            )
                        captured = self._capture(plan, claim)
                        attached = self._attachments.attach(
                            claim,
                            captured.stored,
                            captured.decision,
                            attached_at=self._clock(),
                        )
                        resolution = self._attachments.resolve(
                            claim,
                            captured.stored,
                            resolved_at=self._clock(),
                        )
                        receipt_id = attached.receipt_id
                        if resolution.action == "stop":
                            return self._result(
                                "stop",
                                window.window_id,
                                current.pass_id,
                                page.page_id,
                                receipt_id,
                                error=resolution.failure_code,
                            )
                        if resolution.action == "retry":
                            return self._result(
                                "retry",
                                window.window_id,
                                current.pass_id,
                                page.page_id,
                                receipt_id,
                                error=resolution.failure_code,
                            )

                if receipt_id is None:
                    return self._result(
                        "replay_failed",
                        window.window_id,
                        current.pass_id,
                        page.page_id,
                        None,
                        error="crossref_receipt_missing",
                    )
                last_receipt = receipt_id
                replayed = self._replay(
                    plan,
                    request,
                    receipt_id,
                )
                self._journal.save_decoded(
                    page.page_id,
                    replayed,
                    self._clock(),
                )
                pending = self._journal.pending_items(page.page_id)
                if pending:
                    return self._result(
                        "projection_required",
                        window.window_id,
                        current.pass_id,
                        page.page_id,
                        receipt_id,
                        len(pending),
                    )
                current = self._journal.commit_page(
                    current.pass_id,
                    page.page_id,
                    self._clock(),
                )
            except CrossrefCaptureClaimError as exc:
                return self._claim_error(
                    exc,
                    window.window_id,
                    current.pass_id,
                    last_page_id or "",
                )
            except CrossrefRateError as exc:
                state = "retry" if exc.code in _RETRY_RATE_CODES else "stop"
                return self._result(
                    state,
                    window.window_id,
                    current.pass_id,
                    last_page_id,
                    last_receipt,
                    error=exc.code,
                )
            except (
                CrossrefAttachmentError,
                CrossrefCaptureError,
                CrossrefCaptureInboxError,
                CrossrefHarvestJournalError,
                CrossrefProtocolError,
                StorageError,
            ) as exc:
                return self._result(
                    "replay_failed",
                    window.window_id,
                    current.pass_id,
                    last_page_id,
                    last_receipt,
                    error=getattr(exc, "code", "crossref_local_recovery_failed"),
                )

        return self._result(
            "page_committed",
            window.window_id,
            current.pass_id,
            last_page_id,
            last_receipt,
            error="crossref_page_budget",
        )
