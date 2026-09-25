from datetime import datetime

from libs.discovery.ports.list_source_observations_port import (
    ListSourceObservationsPort,
)
from libs.discovery.ports.replay_arxiv_observation_port import (
    ReplayArxivObservationPort,
)
from libs.discovery.ports.replay_pubmed_observation_port import (
    ReplayPubmedObservationPort,
)
from libs.research_workflow.dtos.source_catalog_projection import (
    SourceCatalogProjectionResult,
)
from libs.research_workflow.exceptions.source_catalog_projection_error import (
    SourceCatalogProjectionError as Error,
)
from libs.research_workflow.ports.source_catalog_projection_store_port import (
    SourceCatalogProjectionStorePort,
)
from libs.scholarly_catalog.ports.project_arxiv_observation_port import (
    ProjectArxivObservationPort,
)
from libs.scholarly_catalog.ports.project_pubmed_observation_port import (
    ProjectPubmedObservationPort,
)


class ProjectSourceCatalogUnit:
    _SOURCES = frozenset({"arxiv", "pubmed"})

    def __init__(
        self,
        observations: ListSourceObservationsPort,
        store: SourceCatalogProjectionStorePort,
        arxiv_replay: ReplayArxivObservationPort,
        arxiv_project: ProjectArxivObservationPort,
        pubmed_replay: ReplayPubmedObservationPort,
        pubmed_project: ProjectPubmedObservationPort,
    ) -> None:
        self._observations = observations
        self._store = store
        self._arxiv_replay = arxiv_replay
        self._arxiv_project = arxiv_project
        self._pubmed_replay = pubmed_replay
        self._pubmed_project = pubmed_project

    def __call__(
        self,
        source: str,
        unit_id: str,
        *,
        max_observations: int,
        projected_at: datetime,
    ) -> SourceCatalogProjectionResult:
        if source not in self._SOURCES:
            raise Error("unsupported_source_catalog_projection_source")
        if type(max_observations) is not int or not 1 <= max_observations <= 1000:
            raise Error("invalid_source_catalog_projection_limit")
        progress = self._store.ensure(unit_id, source, projected_at)
        if progress.state == "succeeded":
            return SourceCatalogProjectionResult(
                unit_id,
                source,
                "succeeded",
                progress.projected_count,
                0,
                progress.last_observation_id,
            )

        page = self._observations(
            unit_id,
            source,
            after_observation_id=progress.last_observation_id,
            limit=max_observations,
        )
        if page.unit_id != unit_id or page.source != source:
            raise Error("source_catalog_projection_page_mismatch")

        processed = 0
        for observation_id in page.observation_ids:
            if source == "arxiv":
                replay = self._arxiv_replay(observation_id)
                if (
                    replay.observation_id != observation_id
                    or replay.unit_id != unit_id
                ):
                    raise Error("source_catalog_projection_replay_mismatch")
                result = self._arxiv_project(replay)
            else:
                replay = self._pubmed_replay(observation_id)
                if (
                    replay.observation_id != observation_id
                    or replay.unit_id != unit_id
                ):
                    raise Error("source_catalog_projection_replay_mismatch")
                result = self._pubmed_project(replay)
            if result.observation_id != observation_id:
                raise Error("source_catalog_projection_result_mismatch")
            progress = self._store.advance(
                unit_id,
                source,
                expected_checkpoint_version=progress.checkpoint_version,
                expected_after_observation_id=progress.last_observation_id,
                observation_id=observation_id,
                updated_at=projected_at,
            )
            processed += 1

        if page.complete:
            progress = self._store.complete(
                unit_id,
                source,
                expected_checkpoint_version=progress.checkpoint_version,
                updated_at=projected_at,
            )
        state = "succeeded" if progress.state == "succeeded" else "budget_exhausted"
        return SourceCatalogProjectionResult(
            unit_id,
            source,
            state,
            progress.projected_count,
            processed,
            progress.last_observation_id,
        )
