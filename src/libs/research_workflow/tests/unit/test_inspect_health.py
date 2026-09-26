from datetime import datetime, timezone

from libs.research_workflow.application.queries.inspect_health import InspectHealth
from libs.research_workflow.dtos.operational_health import (
    DeliveryHealthEvidence,
    ExplanationHealthEvidence,
    RuntimeHealthEvidence,
    WorkflowHealthEvidence,
)
from libs.discovery.dtos.harvest_coverage import HarvestCoverageWindow
from libs.research_workflow.dtos.scheduler import CoverageGap


NOW = datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc)


class Coverage:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self):
        return self.rows


class Workflow:
    def __init__(self, rows):
        self.rows = rows

    def __call__(self):
        return self.rows


class Scheduler:
    def __init__(self, gaps=()):
        self.gaps = gaps

    def read(self, now):
        del now
        return type("Snapshot", (), {"input_gaps": self.gaps})()


class Explanation:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Runtime:
    def __call__(self):
        return RuntimeHealthEvidence(
            python_implementation="cpython",
            python_version="3.13.5",
            sqlite_version="3.49.1",
            sqlite_source_id="2025-02-18 example",
        )


class Delivery:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def window(
    business_key: str,
    source_id: str,
    state: str,
    window_end: str,
    *,
    error: str | None = None,
) -> HarvestCoverageWindow:
    return HarvestCoverageWindow(
        business_key=business_key,
        binding_key=f"binding:{source_id}",
        profile_id="personal",
        profile_revision=3,
        domain_id="statistics",
        domain_revision=7,
        source_id=source_id,
        window_start="2026-09-25T00:00:00+00:00",
        window_end=window_end,
        workflow_state=state,
        last_error_code=error,
        population_recall=None,
        crossref_generations=(),
    )


def test_health_keeps_source_failure_and_pending_age_distinct_from_no_new_papers():
    query = InspectHealth(
        coverage=Coverage(
            (
                window(
                    "harvest:arxiv:ok",
                    "arxiv",
                    "succeeded",
                    "2026-09-26T06:00:00+00:00",
                ),
                window(
                    "harvest:pubmed:failed",
                    "pubmed",
                    "failed",
                    "2026-09-26T06:30:00+00:00",
                    error="ncbi_unavailable",
                ),
                window(
                    "harvest:crossref:pending",
                    "crossref",
                    "pending",
                    "2026-09-26T07:00:00+00:00",
                ),
            )
        ),
        scheduler=Scheduler((CoverageGap("harvest", "personal:3:badminton:7", "no_selected_source"),)),
        workflow=Workflow(
            (
                WorkflowHealthEvidence(
                    business_key="harvest:crossref:pending",
                    state="pending",
                    created_at="2026-09-26T07:15:00+00:00",
                    due_at="2026-09-26T07:30:00+00:00",
                    last_error_code=None,
                ),
            )
        ),
        explanation=Explanation(
            ExplanationHealthEvidence(
                qa_rejected=2,
                currency="USD",
                reserved_micros=600,
                settled_actual_micros=533,
                unknown_cost_reservations=1,
            )
        ),
        delivery=Delivery(DeliveryHealthEvidence(unknown_deliveries=1)),
        runtime=Runtime(),
        clock=lambda: NOW,
    )

    result = query()

    by_source = {source.source_id: source for source in result.sources}
    assert by_source["arxiv"].latest_successful_window_end == "2026-09-26T06:00:00+00:00"
    assert by_source["arxiv"].failure_count == 0
    assert by_source["pubmed"].latest_successful_window_end is None
    assert by_source["pubmed"].failure_count == 1
    assert by_source["pubmed"].last_error_code == "ncbi_unavailable"
    assert by_source["crossref"].pending_count == 1
    assert by_source["crossref"].oldest_pending_age_seconds == 2700

    assert result.coverage_gaps == (\n        CoverageGap("harvest", "personal:3:badminton:7", "no_selected_source"),\n    )\n    assert result.explanations.qa_rejected == 2
    assert result.explanations.reserved_micros == 600
    assert result.explanations.settled_actual_micros == 533
    assert result.explanations.unknown_cost_reservations == 1
    assert result.delivery.unknown_deliveries == 1
    assert result.runtime.sqlite_version == "3.49.1"
    assert result.runtime.python_implementation == "cpython"


def test_missing_optional_health_evidence_is_unknown_not_zero():
    query = InspectHealth(
        coverage=Coverage(()),
        workflow=Workflow(()),
        scheduler=Scheduler(),
        explanation=Explanation(None),
        delivery=Delivery(None),
        runtime=Runtime(),
        clock=lambda: NOW,
    )

    result = query()

    assert result.sources == ()
    assert result.coverage_gaps == ()
    assert result.explanations is None
    assert result.delivery is None
