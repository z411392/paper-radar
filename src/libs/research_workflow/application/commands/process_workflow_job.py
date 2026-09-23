import json
from datetime import datetime, timedelta, timezone

from libs.discovery.exceptions.harvest_error import HarvestError
from libs.discovery.exceptions.source_fetch_error import SourceFetchError
from libs.discovery.exceptions.source_query_error import SourceQueryError
from libs.research_workflow.dtos.harvest_query_request import HarvestQueryRequest
from libs.research_workflow.dtos.worker import WorkflowJobProcessResult
from libs.research_workflow.dtos.workflow_job import CompleteWorkflowJob
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.research_workflow.exceptions.workflow_job_error import WorkflowJobError
from libs.research_workflow.ports.build_harvest_query_input_port import BuildHarvestQueryInputPort
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
    ) -> None:
        self._store = store
        self._builder = builder
        self._harvest = harvest
        self._clock = clock
        self._live_source_enabled = live_source_enabled

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

    def _harvest_job(self, lease) -> WorkflowJobProcessResult:
        if not self._live_source_enabled or self._harvest is None:
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
        request = HarvestQueryRequest(
            profile_id=data["profile_id"],
            domain_id=data["domain_id"],
            window_start=self._instant(data["window_start"]),
            window_end=self._instant(data["window_end"]),
            source_id=data["source_id"],
            deferred_mode="defer",
            page_size=200,
            expected_profile_revision=data["profile_revision"],
            expected_domain_revision=data["domain_revision"],
        )
        try:
            query = self._builder(request)
            result = self._harvest(query, max_pages=10, retry_failed=True)
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
            return self._harvest_job(lease)
        if lease.job_kind == "prepare_digest":
            return self._defer(
                lease,
                error_code="digest_candidate_pipeline_not_connected",
                delay=timedelta(hours=1),
                state="awaiting_external",
            )
        return self._complete(
            lease,
            state="cancelled",
            error_code="unsupported_workflow_job_kind",
            next_due_at=None,
        )
