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
from libs.research_workflow.exceptions.operational_health_error import (
    OperationalHealthError,
)
from libs.research_workflow.ports.operational_health_port import (
    ReadDeliveryHealthEvidencePort,
    ReadExplanationHealthEvidencePort,
    ReadWorkflowHealthEvidencePort,
)


_PENDING_STATES = frozenset({"pending", "running", "awaiting_external", "budget_blocked"})


class InspectHealth:
    def __init__(
        self,
        *,
        coverage: ReadHarvestCoveragePort,
        workflow: ReadWorkflowHealthEvidencePort,
        explanation: ReadExplanationHealthEvidencePort,
        delivery: ReadDeliveryHealthEvidencePort,
        clock: Callable[[], datetime],
    ) -> None:
        self._coverage = coverage
        self._workflow = workflow
        self._explanation = explanation
        self._delivery = delivery
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

    @classmethod
    def _source(
        cls,
        source_id: str,
        rows: list[HarvestCoverageWindow],
        jobs: dict[str, WorkflowHealthEvidence],
        now: datetime,
    ) -> SourceOperationalHealth:
        latest = max(rows, key=lambda item: cls._instant(item.window_end))
        succeeded = [row for row in rows if row.workflow_state == "succeeded"]
        latest_success = (
            max(succeeded, key=lambda item: cls._instant(item.window_end)).window_end
            if succeeded
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
            source_id=source_id,
            latest_successful_window_end=latest_success,
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

        workflow_rows = self._workflow()
        jobs: dict[str, WorkflowHealthEvidence] = {}
        for row in workflow_rows:
            if row.business_key in jobs:
                raise OperationalHealthError("duplicate_workflow_health_evidence")
            jobs[row.business_key] = row

        grouped: dict[str, list[HarvestCoverageWindow]] = defaultdict(list)
        for row in self._coverage():
            grouped[row.source_id].append(row)

        sources = tuple(
            self._source(source_id, grouped[source_id], jobs, now)
            for source_id in sorted(grouped)
        )
        return OperationalHealthReport(
            sources=sources,
            explanations=self._explanation(),
            delivery=self._delivery(),
        )
