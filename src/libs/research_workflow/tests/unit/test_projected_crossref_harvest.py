from datetime import datetime, timezone

from libs.discovery.dtos.crossref_harvest import (
    CrossrefHarvestStepResult,
    CrossrefPendingItem,
)
from libs.research_workflow.application.commands.run_projected_crossref_harvest_window import (
    RunProjectedCrossrefHarvestWindow,
)
from libs.scholarly_catalog.dtos.crossref_provider_revision import (
    CrossrefProviderProjection,
)
from libs.scholarly_catalog.exceptions.crossref_provider_projection_error import (
    CrossrefProviderProjectionError,
)


NOW = datetime(2026, 9, 25, 6, 0, tzinfo=timezone.utc)


class Harvest:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def __call__(self, plan, *, owner_id, max_pages, lease_seconds):
        del plan, owner_id, lease_seconds
        assert max_pages == 1
        self.calls += 1
        return self.values.pop(0)


class Journal:
    def __init__(self, pending):
        self.pending = {key: list(value) for key, value in pending.items()}
        self.processed = []
        self.errors = []
        self.quarantined = []

    def pending_items(self, page_id):
        return tuple(self.pending.get(page_id, ()))

    def mark_processed(
        self,
        page_id,
        ordinal,
        *,
        canonical_doi,
        outcome_ref,
        processed_at,
    ):
        self.processed.append(
            (page_id, ordinal, canonical_doi, outcome_ref, processed_at)
        )
        self.pending[page_id] = [
            item
            for item in self.pending[page_id]
            if item.ordinal != ordinal
        ]
        return False

    def mark_projection_quarantined(
        self,
        page_id,
        ordinal,
        *,
        error_code,
        quarantined_at,
    ):
        self.quarantined.append(
            (page_id, ordinal, error_code, quarantined_at)
        )
        self.pending[page_id] = [
            item
            for item in self.pending[page_id]
            if item.ordinal != ordinal
        ]
        return False

    def note_page_error(self, page_id, error_code):
        self.errors.append((page_id, error_code))


class Project:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    def __call__(self, pending, *, observed_at):
        self.calls.append((pending, observed_at))
        if self.error is not None:
            raise CrossrefProviderProjectionError(self.error)
        return CrossrefProviderProjection(
            "10.1000/a",
            "crossref-provider-revision:test",
            "a" * 64,
            None,
            None,
            None,
            False,
        )


def item():
    return CrossrefPendingItem(
        "crossref-page:1",
        0,
        "10.1000/A",
        '{"DOI":"10.1000/A"}',
        "b" * 64,
    )


def result(state, page="crossref-page:1", pending=1, error=None):
    return CrossrefHarvestStepResult(
        state,
        "window:1",
        "pass:1",
        page,
        "raw:" + "c" * 64,
        pending,
        error,
    )


def test_projection_marks_item_then_runner_can_complete() -> None:
    harvest = Harvest(
        [
            result("projection_required"),
            result("page_committed", pending=0, error="crossref_page_budget"),
            result("pass_completed", pending=0, error=None),
        ]
    )
    journal = Journal({"crossref-page:1": [item()]})
    project = Project()
    runner = RunProjectedCrossrefHarvestWindow(
        harvest,
        journal,
        project,
        clock=lambda: NOW,
    )

    outcome = runner(object(), owner_id="worker:test", max_pages=2, lease_seconds=60)

    assert outcome.state == "pass_completed"
    assert len(project.calls) == 1
    assert journal.processed == [
        (
            "crossref-page:1",
            0,
            "10.1000/a",
            "crossref-provider-revision:test",
            NOW,
        )
    ]


def test_invalid_doi_is_durably_quarantined_and_does_not_block_page() -> None:
    harvest = Harvest(
        [
            result("projection_required"),
            result("page_committed", pending=0, error="crossref_page_budget"),
            result("pass_completed", pending=0, error=None),
        ]
    )
    journal = Journal({"crossref-page:1": [item()]})
    project = Project(error="crossref_provider_doi_invalid")
    runner = RunProjectedCrossrefHarvestWindow(
        harvest,
        journal,
        project,
        clock=lambda: NOW,
    )

    outcome = runner(object(), owner_id="worker:test", max_pages=2, lease_seconds=60)

    assert outcome.state == "pass_completed"
    assert journal.processed == []
    assert journal.errors == []
    assert journal.quarantined == [
        (
            "crossref-page:1",
            0,
            "crossref_provider_doi_invalid",
            NOW,
        )
    ]


def test_projection_system_failure_stays_pending_and_records_page_error() -> None:
    harvest = Harvest([result("projection_required")])
    journal = Journal({"crossref-page:1": [item()]})
    project = Project(error="crossref_provider_database_error")
    runner = RunProjectedCrossrefHarvestWindow(
        harvest,
        journal,
        project,
        clock=lambda: NOW,
    )

    outcome = runner(object(), owner_id="worker:test", max_pages=2, lease_seconds=60)

    assert outcome.state == "projection_failed"
    assert outcome.error_code == "crossref_provider_database_error"
    assert outcome.pending_item_count == 1
    assert journal.processed == []
    assert journal.quarantined == []
    assert journal.errors == [
        ("crossref-page:1", "crossref_provider_database_error")
    ]


def test_page_budget_does_not_fetch_another_page_after_projection() -> None:
    harvest = Harvest([result("projection_required")])
    journal = Journal({"crossref-page:1": [item()]})
    runner = RunProjectedCrossrefHarvestWindow(
        harvest,
        journal,
        Project(),
        clock=lambda: NOW,
    )

    outcome = runner(object(), owner_id="worker:test", max_pages=1, lease_seconds=60)

    assert outcome.state == "page_committed"
    assert outcome.error_code == "crossref_page_budget"
    assert harvest.calls == 1
