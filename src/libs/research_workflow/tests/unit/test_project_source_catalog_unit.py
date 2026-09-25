from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from libs.discovery.dtos.source_observation_page import SourceObservationPage
from libs.research_workflow.application.commands.project_source_catalog_unit import (
    ProjectSourceCatalogUnit,
)
from libs.research_workflow.dtos.source_catalog_projection import (
    SourceCatalogProjectionProgress,
)


NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)
UNIT = "unit:" + "a" * 64
O1 = "observation:" + "1" * 64
O2 = "observation:" + "2" * 64


class Store:
    def __init__(self) -> None:
        self.progress = SourceCatalogProjectionProgress(
            UNIT,
            "arxiv",
            None,
            0,
            "running",
            0,
            NOW,
            NOW,
        )

    def ensure(self, unit_id, source, created_at):
        assert (unit_id, source, created_at) == (UNIT, self.progress.source, NOW)
        return self.progress

    def advance(
        self,
        unit_id,
        source,
        *,
        expected_checkpoint_version,
        expected_after_observation_id,
        observation_id,
        updated_at,
    ):
        assert unit_id == UNIT and source == self.progress.source
        assert expected_checkpoint_version == self.progress.checkpoint_version
        assert expected_after_observation_id == self.progress.last_observation_id
        self.progress = SourceCatalogProjectionProgress(
            UNIT,
            source,
            observation_id,
            self.progress.projected_count + 1,
            "running",
            self.progress.checkpoint_version + 1,
            NOW,
            updated_at,
        )
        return self.progress

    def complete(
        self,
        unit_id,
        source,
        *,
        expected_checkpoint_version,
        updated_at,
    ):
        assert expected_checkpoint_version == self.progress.checkpoint_version
        self.progress = SourceCatalogProjectionProgress(
            unit_id,
            source,
            self.progress.last_observation_id,
            self.progress.projected_count,
            "succeeded",
            self.progress.checkpoint_version + 1,
            NOW,
            updated_at,
        )
        return self.progress


class ListPage:
    def __init__(self, ids, complete=True):
        self.ids = ids
        self.complete = complete

    def __call__(self, unit_id, source, *, after_observation_id, limit):
        assert unit_id == UNIT
        assert after_observation_id is None
        assert limit == 2
        return SourceObservationPage(unit_id, source, self.ids, self.complete)


def replay(observation_id):
    return SimpleNamespace(observation_id=observation_id, unit_id=UNIT)


def project(value):
    return SimpleNamespace(observation_id=value.observation_id)


def command(page, store=None):
    return ProjectSourceCatalogUnit(
        page,
        store or Store(),
        replay,
        project,
        replay,
        project,
    )


def test_closed_page_projects_each_observation_then_completes() -> None:
    result = command(ListPage((O1, O2)))(
        "arxiv",
        UNIT,
        max_observations=2,
        projected_at=NOW,
    )

    assert result.state == "succeeded"
    assert result.projected_count == 2
    assert result.processed_in_call == 2
    assert result.last_observation_id == O2


def test_budget_exhaustion_keeps_durable_cursor_for_next_call() -> None:
    result = command(ListPage((O1, O2), complete=False))(
        "arxiv",
        UNIT,
        max_observations=2,
        projected_at=NOW,
    )

    assert result.state == "budget_exhausted"
    assert result.projected_count == 2


def test_projection_failure_never_advances_failed_observation() -> None:
    store = Store()

    def fail(value):
        if value.observation_id == O2:
            raise RuntimeError("projection failed")
        return project(value)

    usecase = ProjectSourceCatalogUnit(
        ListPage((O1, O2)),
        store,
        replay,
        fail,
        replay,
        project,
    )

    with pytest.raises(RuntimeError, match="projection failed"):
        usecase("arxiv", UNIT, max_observations=2, projected_at=NOW)

    assert store.progress.last_observation_id == O1
    assert store.progress.projected_count == 1


def test_completed_projection_replay_is_read_only() -> None:
    store = Store()
    store.progress = SourceCatalogProjectionProgress(
        UNIT,
        "arxiv",
        O2,
        2,
        "succeeded",
        3,
        NOW,
        NOW,
    )

    result = command(ListPage(()), store)(
        "arxiv",
        UNIT,
        max_observations=2,
        projected_at=NOW,
    )

    assert result.state == "succeeded"
    assert result.processed_in_call == 0
