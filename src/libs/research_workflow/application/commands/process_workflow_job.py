import json
from datetime import datetime, timedelta, timezone

from libs.delivery.dtos.scheduled_digest import DigestCoverageGap, ScheduledDigestRequest
from libs.delivery.exceptions.scheduled_digest_error import ScheduledDigestError
from libs.delivery.ports.prepare_scheduled_digest_port import PrepareScheduledDigestPort
from libs.discovery.exceptions.crossref_attachment_error import CrossrefAttachmentError
from libs.discovery.exceptions.crossref_capture_claim_error import CrossrefCaptureClaimError
from libs.discovery.exceptions.crossref_capture_error import CrossrefCaptureError
from libs.discovery.exceptions.crossref_capture_inbox_error import CrossrefCaptureInboxError
from libs.discovery.exceptions.crossref_harvest_journal_error import CrossrefHarvestJournalError
from libs.discovery.exceptions.crossref_protocol_error import CrossrefProtocolError
from libs.discovery.exceptions.crossref_rate_error import CrossrefRateError
from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.exceptions.source_query_error import SourceQueryError
from libs.discovery.ports.run_pubmed_harvest_window_port import RunPubmedHarvestWindowPort
from libs.research_workflow.ports.build_crossref_window_plan_port import (
    BuildCrossrefWindowPlanPort,
)
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest
from libs.research_workflow.dtos.worker import WorkflowJobProcessResult
from libs.research_workflow.dtos.workflow_job import CompleteWorkflowJob
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
from libs.research_workflow.ports.run_crossref_harvest_window_port import (
    RunCrossrefHarvestWindowPort,
)
from libs.research_workflow.ports.run_harvest_slice_port import RunHarvestSlicePort
from libs.research_workflow.ports.workflow_clock_port import WorkflowClockPort
from libs.research_workflow.ports.workflow_job_store_port import WorkflowJobStorePort


class ProcessWorkflowJob:
    _STALE_CODES = frozenset(
        {
            "scheduled_input_stale",
            "profile_not_current",
            "domain_not_selected",
            "source_not_selected",
            "invalid_profile_snapshot",
            "invalid_domain_snapshot",
        }
    )

    def __init__(
        self,
        *,
        store: WorkflowJobStorePort,
        builder: BuildHarvestQueryInputPort,
        harvest: RunHarvestSlicePort | None,
        clock: WorkflowClockPort,
        live_source_enabled: bool,
        digest: PrepareScheduledDigestPort | None = None,
        pubmed: RunPubmedHarvestWindowPort | None = None,
        crossref_plan: BuildCrossrefWindowPlanPort | None = None,
        crossref: RunCrossrefHarvestWindowPort | None = None,
    ) -> None:
        self._store = store
        self._builder = builder
        self._harvest = harvest
        self._clock = clock
        self._live_source_enabled = live_source_enabled
        self._digest = digest
        self._pubmed = pubmed
        self._crossref_plan = crossref_plan
        self._crossref = crossref

    @staticmethod
    def _instant(value: object) -> datetime:
        if not isinstance(value, str):
            raise WorkflowJobError("invalid_job_payload")
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise WorkflowJobError("invalid_job_payload") from None
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise WorkflowJobError("invalid_job_payload")
        return moment.astimezone(timezone.utc)

    @staticmethod
    def _payload(value: str) -> dict:
        def pairs(rows: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in rows:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = item
            return result

        try:
            data = json.loads(value, object_pairs_hook=pairs)
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            raise WorkflowJobError("invalid_job_payload") from None
        if not isinstance(data, dict):
            raise WorkflowJobError("invalid_job_payload")
        return data

    def _complete(
        self,
        lease,
        *,
        state: str,
        error_code: str | None,
        next_due_at: datetime | None,
    ) -> WorkflowJobProcessResult:
        finished = self._clock.now()
        outcome = self._store.complete(
            CompleteWorkflowJob(
                job_id=lease.job_id,
                owner_id=lease.owner_id,
                fencing_token=lease.fencing_token,
                state=state,
                error_code=error_code,
                finished_at=finished,
                next_due_at=next_due_at,
            )
        )
        return WorkflowJobProcessResult(
            outcome.state,
            lease.job_id,
            lease.job_kind,
            error_code,
        )

    def _defer(
        self,
        lease,
        *,
        error_code: str,
        delay: timedelta,
        state: str = "failed",
    ) -> WorkflowJobProcessResult:
        base = self._clock.now()
        outcome = self._store.complete(
            CompleteWorkflowJob(
                job_id=lease.job_id,
                owner_id=lease.owner_id,
                fencing_token=lease.fencing_token,
                state=state,
                error_code=error_code,
                finished_at=base,
                next_due_at=base + delay,
            )
        )
        return WorkflowJobProcessResult(
            outcome.state,
            lease.job_id,
            lease.job_kind,
            error_code,
        )

    def _harvest_job(
        self,
        lease,
        *,
        lease_seconds: int,
    ) -> WorkflowJobProcessResult:
        if not self._live_source_enabled:
            return self._defer(
                lease,
                error_code="live_source_not_authorized",
                delay=timedelta(hours=1),
                state="awaiting_external",
            )
        data = self._payload(lease.input_json)
        required = {
            "binding_key",
            "profile_id",
            "profile_revision",
            "domain_id",
            "domain_revision",
            "source_id",
            "window_start",
            "window_end",
        }
        if set(data) != required:
            raise WorkflowJobError("invalid_job_payload")
        if (
            not isinstance(data["profile_id"], str)
            or type(data["profile_revision"]) is not int
            or not isinstance(data["domain_id"], str)
            or type(data["domain_revision"]) is not int
            or not isinstance(data["source_id"], str)
        ):
            raise WorkflowJobError("invalid_job_payload")
        source_id = data["source_id"]
        request = HarvestQueryRequest(
            profile_id=data["profile_id"],
            domain_id=data["domain_id"],
            window_start=self._instant(data["window_start"]),
            window_end=self._instant(data["window_end"]),
            source_id=source_id,
            deferred_mode="defer",
            time_basis=(
                "createDate"
                if source_id == "pubmed"
                else "indexDate"
                if source_id == "crossref"
                else "submittedDate"
            ),
            page_size=1000 if source_id == "crossref" else 200,
            expected_profile_revision=data["profile_revision"],
            expected_domain_revision=data["domain_revision"],
        )
        try:
            query = self._builder(request)
            if source_id == "crossref":
                runner = self._crossref
                planner = self._crossref_plan
                if runner is None or planner is None:
                    return self._defer(
                        lease,
                        error_code="crossref_runtime_not_connected",
                        delay=timedelta(hours=1),
                        state="awaiting_external",
                    )
                plan = planner(
                    query,
                    binding_key=data["binding_key"],
                )
                result = runner(
                    plan,
                    owner_id=lease.owner_id,
                    max_pages=10,
                    lease_seconds=lease_seconds,
                )
                if result.state == "pass_completed":
                    return self._complete(
                        lease,
                        state="succeeded",
                        error_code=None,
                        next_due_at=None,
                    )
                if result.state == "projection_required":
                    return self._defer(
                        lease,
                        error_code="crossref_projection_required",
                        delay=timedelta(hours=1),
                        state="awaiting_external",
                    )
                if result.state == "page_committed":
                    return self._defer(
                        lease,
                        error_code="crossref_page_budget",
                        delay=timedelta(minutes=1),
                    )
                state = (
                    "awaiting_external"
                    if result.state in {"stop", "replay_failed"}
                    else "failed"
                )
                return self._defer(
                    lease,
                    error_code=result.error_code or "crossref_harvest_incomplete",
                    delay=timedelta(hours=1) if state == "awaiting_external" else timedelta(minutes=5),
                    state=state,
                )
            if source_id == "pubmed":
                pubmed = self._pubmed
                if pubmed is None:
                    return self._defer(
                        lease,
                        error_code="pubmed_runtime_not_connected",
                        delay=timedelta(hours=1),
                        state="awaiting_external",
                    )
                result = pubmed(
                    query,
                    started_at=self._clock.now(),
                    max_batches=10,
                )
            else:
                harvest = self._harvest
                if harvest is None:
                    return self._defer(
                        lease,
                        error_code="arxiv_runtime_not_connected",
                        delay=timedelta(hours=1),
                        state="awaiting_external",
                    )
                result = harvest(query, max_pages=10, retry_failed=True)
        except HarvestWorkflowError as exc:
            if exc.code in self._STALE_CODES:
                return self._complete(
                    lease,
                    state="cancelled",
                    error_code=exc.code,
                    next_due_at=None,
                )
            return self._defer(
                lease,
                error_code=exc.code,
                delay=timedelta(minutes=5),
            )
        except SourceFetchError as exc:
            delay = (
                timedelta(seconds=max(1.0, exc.retry_after_seconds))
                if exc.retry_after_seconds is not None
                else timedelta(minutes=5)
            )
            return self._defer(lease, error_code=exc.code, delay=delay)
        except (
            CrossrefAttachmentError,
            CrossrefCaptureClaimError,
            CrossrefCaptureError,
            CrossrefCaptureInboxError,
            CrossrefHarvestJournalError,
            CrossrefProtocolError,
            CrossrefRateError,
        ) as exc:
            retry_after = getattr(exc, "retry_after_seconds", None)
            delay = (
                timedelta(seconds=max(1.0, retry_after))
                if retry_after is not None
                else timedelta(minutes=5)
            )
            state = (
                "awaiting_external"
                if any(
                    token in exc.code
                    for token in (
                        "corrupt",
                        "mismatch",
                        "unknown",
                        "disabled",
                        "missing",
                        "unavailable",
                        "circuit",
                    )
                )
                else "failed"
            )
            return self._defer(
                lease,
                error_code=exc.code,
                delay=delay,
                state=state,
            )
        except (HarvestError, SourceQueryError) as exc:
            return self._defer(
                lease,
                error_code=exc.code,
                delay=timedelta(minutes=5),
            )

        if result.stop_reason == "complete":
            return self._complete(
                lease,
                state="succeeded",
                error_code=None,
                next_due_at=None,
            )
        if result.stop_reason == "profile_changed":
            return self._complete(
                lease,
                state="cancelled",
                error_code="scheduled_input_stale",
                next_due_at=None,
            )
        if result.stop_reason == "unavailable":
            return self._defer(
                lease,
                error_code="harvest_unavailable",
                delay=timedelta(hours=24),
                state="awaiting_external",
            )
        delay = timedelta(minutes=1) if result.stop_reason == "page_budget" else timedelta(minutes=5)
        return self._defer(
            lease,
            error_code="harvest_" + result.stop_reason,
            delay=delay,
        )

    @staticmethod
    def _coverage(value: object) -> tuple[DigestCoverageGap, ...]:
        if not isinstance(value, list) or len(value) > 128:
            raise WorkflowJobError("invalid_job_payload")
        result = []
        for row in value:
            if not isinstance(row, dict) or set(row) != {"kind", "identity", "reason"}:
                raise WorkflowJobError("invalid_job_payload")
            if any(
                not isinstance(row[key], str) or not row[key].strip()
                for key in ("kind", "identity", "reason")
            ):
                raise WorkflowJobError("invalid_job_payload")
            result.append(DigestCoverageGap(row["kind"], row["identity"], row["reason"]))
        return tuple(result)

    def _digest_job(self, lease) -> WorkflowJobProcessResult:
        digest = self._digest
        if digest is None:
            return self._defer(
                lease,
                error_code="digest_candidate_pipeline_not_connected",
                delay=timedelta(hours=1),
                state="awaiting_external",
            )
        data = self._payload(lease.input_json)
        if set(data) != {
            "subscription_id",
            "period_key",
            "period_start",
            "cutoff_at",
            "coverage_gaps",
        }:
            raise WorkflowJobError("invalid_job_payload")
        if (
            not isinstance(data["subscription_id"], str)
            or not isinstance(data["period_key"], str)
        ):
            raise WorkflowJobError("invalid_job_payload")
        request = ScheduledDigestRequest(
            subscription_id=data["subscription_id"],
            period_key=data["period_key"],
            period_start=self._instant(data["period_start"]),
            cutoff_at=self._instant(data["cutoff_at"]),
            coverage_gaps=self._coverage(data["coverage_gaps"]),
        )
        try:
            digest(request, created_at=self._clock.now())
        except ScheduledDigestError as exc:
            if (
                exc.code in {"delivery_subscription_missing", "unsupported_digest_channel"}
                or exc.code.startswith("invalid_")
            ):
                return self._complete(
                    lease,
                    state="cancelled",
                    error_code=exc.code,
                    next_due_at=None,
                )
            state = (
                "awaiting_external"
                if any(part in exc.code for part in ("corrupt", "mismatch", "missing"))
                else "failed"
            )
            delay = timedelta(hours=1) if state == "awaiting_external" else timedelta(minutes=5)
            return self._defer(
                lease,
                error_code=exc.code,
                delay=delay,
                state=state,
            )
        return self._complete(
            lease,
            state="succeeded",
            error_code=None,
            next_due_at=None,
        )

    def __call__(
        self,
        owner_id: str,
        *,
        lease_seconds: int,
    ) -> WorkflowJobProcessResult:
        lease = self._store.claim_due(
            owner_id,
            now=self._clock.now(),
            lease_seconds=lease_seconds,
        )
        if lease is None:
            return WorkflowJobProcessResult("idle", None, None, None)
        if lease.job_kind == "harvest_window":
            return self._harvest_job(
                lease,
                lease_seconds=lease_seconds,
            )
        if lease.job_kind == "prepare_digest":
            return self._digest_job(lease)
        return self._complete(
            lease,
            state="cancelled",
            error_code="unsupported_workflow_job_kind",
            next_due_at=None,
        )
