from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone

from libs.discovery.dtos.harvest_coverage import HarvestCoverageWindow
from libs.discovery.ports.harvest_coverage_store_port import ReadHarvestCoveragePort
from libs.research_workflow.dtos.operational_health import (
    OperationalHealthReport,
    SourceOperationalHealth,
    WorkflowHealthEvidence,
)
from libs.research_workflow.dtos.scheduler import HarvestBindingSchedule
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
)
from libs.research_workflow.ports.operational_health_port import (
    ReadDeliveryHealthEvidencePort,
    ReadExplanationHealthEvidencePort,
    ReadRuntimeHealthEvidencePort,
    ReadWorkflowHealthEvidencePort,
)
from libs.research_workflow.ports.scheduler_input_port import SchedulerInputPort


_PENDING_STATES = frozenset({"pending", "running", "awaiting_external", "budget_blocked"})


class InspectHealth:
    def __init__(
        self,
        *,
        coverage: ReadHarvestCoveragePort,
        workflow: ReadWorkflowHealthEvidencePort,
        scheduler: SchedulerInputPort,
        explanation: ReadExplanationHealthEvidencePort,
        delivery: ReadDeliveryHealthEvidencePort,
        runtime: ReadRuntimeHealthEvidencePort,
        clock: Callable[[], datetime],
    ) -> None:
        self._coverage = coverage
        self._workflow = workflow
        self._scheduler = scheduler
        self._explanation = explanation
        self._delivery = delivery
        self._runtime = runtime
        self._clock = clock

    @staticmethod
    def _instant(value: str) -> datetime:
        if not isinstance(value, str):
            raise OperationalHealthError("operational_health_corrupt")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            raise OperationalHealthError("operational_health_corrupt") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise OperationalHealthError("operational_health_corrupt")
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _age(
        cls,
        now: datetime,
        row: WorkflowHealthEvidence,
    ) -> int | None:
        if row.state not in _PENDING_STATES:
            return None
        created = cls._instant(row.created_at)
        seconds = int((now - created).total_seconds())
        if seconds < 0:
            raise OperationalHealthError("operational_health_clock_skew")
        return seconds

    @staticmethod
    def _binding_identity(
        binding: HarvestBindingSchedule,
    ) -> tuple[str, str, int, str, int, str]:
        return (
            binding.binding_key,
            binding.profile_id,
            binding.profile_revision,
            binding.domain_id,
            binding.domain_revision,
            binding.source_id,
        )

    @staticmethod
    def _row_identity(
        row: HarvestCoverageWindow,
    ) -> tuple[str, str, int, str, int, str]:
        return (
            row.binding_key,
            row.profile_id,
            row.profile_revision,
            row.domain_id,
            row.domain_revision,
            row.source_id,
        )

    @classmethod
    def _source(
        cls,
        binding_key: str,
        binding: HarvestBindingSchedule | None,
        rows: list[HarvestCoverageWindow],
        jobs: dict[str, WorkflowHealthEvidence],
        now: datetime,
    ) -> SourceOperationalHealth:
        if not rows:
            if binding is None:
                raise OperationalHealthError("operational_health_binding_missing")
            return SourceOperationalHealth(
                binding_key=binding.binding_key,
                profile_id=binding.profile_id,
                profile_revision=binding.profile_revision,
                domain_id=binding.domain_id,
                domain_revision=binding.domain_revision,
                source_id=binding.source_id,
                evidence_state="vacuum",
                latest_successful_window_end=None,
                latest_successful_at=None,
                latest_observed_window_end=None,
                failure_count=0,
                pending_count=0,
                oldest_pending_age_seconds=None,
                last_error_code=None,
            )

        identity = cls._row_identity(rows[0])
        if identity[0] != binding_key:
            raise OperationalHealthError("operational_health_binding_mismatch")
        if any(cls._row_identity(row) != identity for row in rows[1:]):
            raise OperationalHealthError("operational_health_binding_mismatch")
        if binding is not None and cls._binding_identity(binding) != identity:
            raise OperationalHealthError("operational_health_binding_mismatch")

        latest = max(rows, key=lambda item: cls._instant(item.window_end))
        succeeded = [row for row in rows if row.workflow_state == "succeeded"]
        latest_success = (
            max(succeeded, key=lambda item: cls._instant(item.window_end)).window_end
            if succeeded
            else None
        )
        successful_attempts = [
            jobs[row.business_key].finished_at
            for row in succeeded
            if row.business_key in jobs
            and jobs[row.business_key].finished_at is not None
        ]
        latest_successful_at = (
            max(successful_attempts, key=cls._instant)
            if successful_attempts
            else None
        )
        failures = [row for row in rows if row.workflow_state == "failed"]
        pending = [row for row in rows if row.workflow_state in _PENDING_STATES]
        pending_ages = [
            age
            for row in pending
            if (job := jobs.get(row.business_key)) is not None
            if (age := cls._age(now, job)) is not None
        ]
        errored = [row for row in rows if row.last_error_code is not None]
        last_error = (
            max(errored, key=lambda item: cls._instant(item.window_end)).last_error_code
            if errored
            else None
        )
        return SourceOperationalHealth(
            binding_key=identity[0],
            profile_id=identity[1],
            profile_revision=identity[2],
            domain_id=identity[3],
            domain_revision=identity[4],
            source_id=identity[5],
            evidence_state="observed",
            latest_successful_window_end=latest_success,
            latest_successful_at=latest_successful_at,
            latest_observed_window_end=latest.window_end,
            failure_count=len(failures),
            pending_count=len(pending),
            oldest_pending_age_seconds=max(pending_ages) if pending_ages else None,
            last_error_code=last_error,
        )

    def __call__(self) -> OperationalHealthReport:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise OperationalHealthError("invalid_health_clock")
        now = now.astimezone(timezone.utc)

        scheduler = self._scheduler.read(now)
        workflow_rows = self._workflow()
        jobs: dict[str, WorkflowHealthEvidence] = {}
        for row in workflow_rows:
            if row.business_key in jobs:
                raise OperationalHealthError("duplicate_workflow_health_evidence")
            jobs[row.business_key] = row

        grouped: dict[str, list[HarvestCoverageWindow]] = defaultdict(list)
        for row in self._coverage():
            grouped[row.binding_key].append(row)

        expected: dict[str, HarvestBindingSchedule] = {}
        for binding in scheduler.harvest_bindings:
            if binding.binding_key in expected:
                raise OperationalHealthError("duplicate_health_binding")
            expected[binding.binding_key] = binding

        binding_keys = set(expected) | set(grouped)
        sources = tuple(
            self._source(
                binding_key,
                expected.get(binding_key),
                grouped[binding_key],
                jobs,
                now,
            )
            for binding_key in sorted(binding_keys)
        )
        return OperationalHealthReport(
            sources=sources,
            coverage_gaps=scheduler.input_gaps,
            explanations=self._explanation(),
            delivery=self._delivery(),
            runtime=self._runtime(),
        )
