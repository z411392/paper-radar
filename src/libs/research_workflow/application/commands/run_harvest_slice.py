import math

from libs.discovery.dtos.source_fetch_result import SourceFetchResult
from libs.discovery.dtos.source_query_input import SourceQueryInput
from libs.discovery.ports.fetch_source_page_port import FetchSourcePagePort
from libs.discovery.ports.process_harvest_page_port import ProcessHarvestPagePort
from libs.discovery.ports.read_harvest_resume_port import ReadHarvestResumePort
from libs.discovery.ports.record_harvest_capture_port import RecordHarvestCapturePort
from libs.discovery.ports.source_query_compiler_port import SourceQueryCompilerPort
from libs.discovery.ports.start_harvest_attempt_port import StartHarvestAttemptPort
from libs.research_workflow.domain.services.harvest_run_rules import HarvestRunRules
from libs.research_workflow.dtos.harvest_run_result import HarvestRunResult
from libs.research_workflow.exceptions.harvest_workflow_error import HarvestWorkflowError
from libs.research_workflow.ports.harvest_runtime_port import HarvestRuntimePort
from libs.watch_profiles.ports.read_watch_profile_port import ReadWatchProfilePort


class RunHarvestSlice:
    """Bounded composition of public use cases; no network or storage transaction here."""

    _LOCAL_DEFER_MAX_SECONDS = 5.0
    _LOCAL_DEFER_MAX_RETRIES = 2

    def __init__(
        self,
        compiler: SourceQueryCompilerPort,
        resume: ReadHarvestResumePort,
        start: StartHarvestAttemptPort,
        fetch: FetchSourcePagePort,
        record: RecordHarvestCapturePort,
        process: ProcessHarvestPagePort,
        profiles: ReadWatchProfilePort,
        runtime: HarvestRuntimePort,
        parser_version: str,
    ) -> None:
        self._compiler, self._resume, self._start = compiler, resume, start
        self._fetch, self._record, self._process = fetch, record, process
        self._profiles, self._runtime, self._parser_version = profiles, runtime, parser_version

    def _fetch_with_local_defer(self, request) -> SourceFetchResult:
        retries = 0
        while True:
            result = self._fetch(request)
            if result.failure_code != "provider_deferred":
                return result
            delay = result.retry_after_seconds
            if (
                result.retryable is not True
                or isinstance(delay, bool)
                or not isinstance(delay, (int, float))
                or not math.isfinite(delay)
                or not 0 < delay <= self._LOCAL_DEFER_MAX_SECONDS
                or retries >= self._LOCAL_DEFER_MAX_RETRIES
            ):
                return result
            self._runtime.sleep(float(delay))
            retries += 1

    def __call__(
        self, query: SourceQueryInput, *, max_pages: int = 10, retry_failed: bool = False
    ) -> HarvestRunResult:
        HarvestRunRules.limits(max_pages, retry_failed)
        plan = self._compiler.compile(query)
        if not HarvestRunRules.profile_matches(query, self._profiles(query.profile_id)):
            raise HarvestWorkflowError("profile_not_current")
        fetched, processed, reused = 0, 0, 0
        progress = self._resume(plan, self._parser_version)
        reason = "page_budget"
        while processed < max_pages:
            if progress.state in {"succeeded", "verified_empty", "unavailable"}:
                reason = "unavailable" if progress.state == "unavailable" else "complete"
                break
            if not HarvestRunRules.profile_matches(query, self._profiles(query.profile_id)):
                reason = "profile_changed"
                break
            attempt = progress.attempt
            should_fetch = attempt is None or attempt.capture_json is None
            if attempt is not None and progress.previous_result is not None:
                stop = HarvestRunRules.retry_stop(attempt, retry_failed, self._runtime.now())
                if stop is not None:
                    reason = stop
                    break
                should_fetch = True
            if should_fetch:
                request = self._compiler.page(plan, progress.next_start)
                attempt = self._start(plan, request, self._runtime.new_attempt_id(), self._runtime.now())
                if attempt.capture_json is not None:
                    raise HarvestWorkflowError("attempt_identity_collision")
                response = self._fetch_with_local_defer(request)
                fetched += 1
                attempt = self._record(attempt.attempt_id, response, self._runtime.now())
                if not HarvestRunRules.profile_matches(query, self._profiles(query.profile_id)):
                    progress = self._resume(plan, self._parser_version)
                    reason = "profile_changed"
                    break
            else:
                reused += 1
            if attempt is None:
                raise HarvestWorkflowError("resume_attempt_missing")
            result = self._process(attempt.attempt_id, progress.checkpoint_version, self._runtime.now())
            processed += 1
            # A processing receipt is historical. Always read the current unit again.
            progress = self._resume(plan, self._parser_version)
            if result.error_code is not None:
                reason = "source_failed" if attempt.state == "failed" else "processing_failed"
                break
        if progress.state in {"succeeded", "verified_empty"}:
            reason = "complete"
        return HarvestRunResult(progress, reason, fetched, processed, reused)
