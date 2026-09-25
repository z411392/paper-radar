from datetime import datetime, timezone

from libs.delivery.application.commands.prepare_digest import PrepareDigest
from libs.delivery.domain.services.digest_artifact_rules import DigestArtifactError
from libs.delivery.domain.services.digest_selection_rules import DigestSelectionError
from libs.delivery.dtos.digest_preview import DigestCandidate, PrepareDigestRequest
from libs.delivery.dtos.scheduled_digest import (
    DigestCoverageGap,
    ScheduledDigestOutcome,
    ScheduledDigestRequest,
)
from libs.delivery.exceptions.delivery_store_error import DeliveryStoreError
from libs.delivery.exceptions.prior_recipient_error import PriorRecipientError
from libs.delivery.exceptions.scheduled_digest_error import ScheduledDigestError
from libs.delivery.ports.digest_delivery_context_port import DigestDeliveryContextPort
from libs.delivery.ports.prior_recipient_history_port import (
    PriorRecipientHistoryPort,
)
from libs.delivery.ports.queue_digest_port import QueueDigestPort
from libs.kernel.exceptions.storage_error import StorageError
from libs.paper_explanations.exceptions.digest_summary_read_error import DigestSummaryReadError
from libs.paper_explanations.ports.read_digest_current_summary_port import (
    ReadDigestCurrentSummaryPort,
)
from libs.scholarly_catalog.exceptions.digest_event_read_error import DigestEventReadError
from libs.scholarly_catalog.ports.list_digest_research_events_port import (
    ListDigestResearchEventsPort,
)
from libs.watch_profiles.exceptions.digest_relevance_read_error import DigestRelevanceReadError
from libs.watch_profiles.ports.read_digest_relevance_port import ReadDigestRelevancePort


class PrepareScheduledDigest:
    def __init__(
        self,
        *,
        events: ListDigestResearchEventsPort,
        summaries: ReadDigestCurrentSummaryPort,
        relevance: ReadDigestRelevancePort,
        context: DigestDeliveryContextPort,
        queue: QueueDigestPort,
        prior_recipient: PriorRecipientHistoryPort | None = None,
    ) -> None:
        self._events = events
        self._summaries = summaries
        self._relevance = relevance
        self._context = context
        self._queue = queue
        self._prior_recipient = prior_recipient
        self._prepare = PrepareDigest()

    @staticmethod
    def _instant(value: object, code: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ScheduledDigestError(code)
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ScheduledDigestError(code) from None

    @staticmethod
    def _coverage(gaps: tuple[DigestCoverageGap, ...]) -> tuple[str, ...]:
        if not isinstance(gaps, tuple) or len(gaps) > 128:
            raise ScheduledDigestError("invalid_digest_coverage")
        notes = []
        for gap in gaps:
            if not isinstance(gap, DigestCoverageGap):
                raise ScheduledDigestError("invalid_digest_coverage")
            values = (gap.kind, gap.identity, gap.reason)
            if any(
                not isinstance(value, str) or not value.strip() or "\0" in value
                for value in values
            ):
                raise ScheduledDigestError("invalid_digest_coverage")
            notes.append(f"{gap.kind}: {gap.identity} ({gap.reason})")
        return tuple(sorted(set(notes)))

    @staticmethod
    def _translate(exc: Exception) -> ScheduledDigestError:
        code = getattr(exc, "code", None)
        if not isinstance(code, str) or not code:
            code = str(exc)
        return ScheduledDigestError(code or "scheduled_digest_failed")

    def _execute(
        self,
        request: ScheduledDigestRequest,
        *,
        created_at: datetime,
    ) -> ScheduledDigestOutcome:
        period_start = self._instant(request.period_start, "invalid_digest_period")
        cutoff = self._instant(request.cutoff_at, "invalid_digest_period")
        created = self._instant(created_at, "invalid_digest_created_at")
        if period_start >= cutoff:
            raise ScheduledDigestError("invalid_digest_period")
        if request.rebuild_reason is None:
            if request.rebuild_outbox_id is not None:
                raise ScheduledDigestError("invalid_digest_rebuild")
        elif (
            request.rebuild_reason != "current_input_stale"
            or not isinstance(request.rebuild_outbox_id, str)
            or not request.rebuild_outbox_id.strip()
            or "\0" in request.rebuild_outbox_id
        ):
            raise ScheduledDigestError("invalid_digest_rebuild")

        context = self._context.load(request.subscription_id)
        if not context.enabled:
            return ScheduledDigestOutcome("disabled", 0, None, None)
        if context.channel != "email":
            raise ScheduledDigestError("unsupported_digest_channel")

        period_events = self._events(period_start, cutoff)
        latest_paper_by_work = {}
        status_events = []
        for event in period_events:
            if event.event_kind in {"correction", "retraction"}:
                status_events.append(event)
                continue
            current = latest_paper_by_work.get(event.work_id)
            if current is None or (event.observed_at, event.event_id) > (
                current.observed_at,
                current.event_id,
            ):
                latest_paper_by_work[event.work_id] = event
        events = tuple(
            sorted(
                (*latest_paper_by_work.values(), *status_events),
                key=lambda item: (item.observed_at, item.event_id),
            )
        )
        event_ids = tuple(event.event_id for event in events)
        if request.rebuild_reason is None:
            notified = self._context.already_notified(
                context.reader_id,
                context.channel,
                event_ids,
            )
        else:
            assert request.rebuild_outbox_id is not None
            notified = self._context.already_notified_for_rebuild(
                context.reader_id,
                context.channel,
                event_ids,
                request.rebuild_outbox_id,
            )

        candidates = []
        for event in events:
            if event.event_id in notified:
                continue
            if event.event_kind in {"correction", "retraction"}:
                if self._prior_recipient is None:
                    raise ScheduledDigestError(
                        "prior_recipient_check_unavailable"
                    )
                if not self._prior_recipient.contains(
                    context.reader_id,
                    context.channel,
                    event.work_id,
                ):
                    continue
                if event.event_kind == "correction":
                    plain_language = (
                        "來源目前回報這篇研究有更正紀錄，請以來源提供的更正內容為準。",
                    )
                else:
                    plain_language = (
                        "來源目前回報這篇研究有撤稿紀錄，出版狀態已變更。",
                        "這不代表 Paper Radar 自行判定研究結論錯誤。",
                    )
                candidates.append(
                    DigestCandidate(
                        event_id=event.event_id,
                        work_id=event.work_id,
                        summary_id=None,
                        revision_id=None,
                        current_summary_id=None,
                        current_revision_id=None,
                        qa_state="not_applicable",
                        event_at=event.observed_at,
                        priority=100,
                        domains=(),
                        title=event.title,
                        source_url=event.source_url,
                        plain_language=plain_language,
                        item_kind="status_notice",
                        event_kind=event.event_kind,
                    )
                )
                continue

            if event.revision_id is None:
                raise ScheduledDigestError("digest_event_corrupt")
            summary = self._summaries(event.work_id, event.revision_id)
            if summary is None or not summary.plain_language:
                continue
            relevance = self._relevance(
                context.reader_id,
                event.revision_id,
                summary.snapshot_id,
            )
            if not relevance:
                continue
            domains = tuple(sorted(item.domain_id for item in relevance))
            priority = 20 if any(item.decision == "direct" for item in relevance) else 10
            candidates.append(
                DigestCandidate(
                    event_id=event.event_id,
                    work_id=event.work_id,
                    summary_id=summary.summary_id,
                    revision_id=summary.revision_id,
                    current_summary_id=summary.summary_id,
                    current_revision_id=summary.revision_id,
                    qa_state="passed",
                    event_at=event.observed_at,
                    priority=priority,
                    domains=domains,
                    title=event.title,
                    source_url=event.source_url,
                    plain_language=summary.plain_language,
                    event_kind=event.event_kind,
                )
            )

        preview = self._prepare(
            PrepareDigestRequest(
                subscription_id=context.subscription_id,
                period_key=request.period_key,
                cutoff_at=cutoff,
                max_items=context.max_items,
                candidates=tuple(candidates),
                coverage_notes=self._coverage(request.coverage_gaps),
            )
        )
        if not preview.queueable:
            return ScheduledDigestOutcome("empty", 0, None, None)

        queued = self._queue(
            preview,
            reader_id=context.reader_id,
            channel=context.channel,
            workspace_epoch=context.workspace_epoch,
            created_at=created,
            rebuild_reason=request.rebuild_reason,
        )
        return ScheduledDigestOutcome(
            "queued",
            len(preview.items),
            queued.digest_id,
            queued.outbox_id,
        )

    def __call__(
        self,
        request: ScheduledDigestRequest,
        *,
        created_at: datetime,
    ) -> ScheduledDigestOutcome:
        if not isinstance(request, ScheduledDigestRequest):
            raise ScheduledDigestError("invalid_scheduled_digest")
        try:
            return self._execute(request, created_at=created_at)
        except ScheduledDigestError:
            raise
        except (
            DigestEventReadError,
            DigestSummaryReadError,
            DigestRelevanceReadError,
            StorageError,
            DigestSelectionError,
            DigestArtifactError,
            DeliveryStoreError,
            PriorRecipientError,
        ) as exc:
            raise self._translate(exc) from exc
