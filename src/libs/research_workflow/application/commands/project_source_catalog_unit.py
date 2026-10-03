import hashlib
import json
from datetime import datetime

from libs.discovery.ports.list_source_observations_port import (
    ListSourceObservationsPort,
)
from libs.discovery.ports.read_harvest_unit_context_port import (
    ReadHarvestUnitContextPort,
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
from libs.research_workflow.dtos.workflow_job import EnqueueWorkflowJob
from libs.research_workflow.exceptions.source_catalog_projection_error import (
    SourceCatalogProjectionError as Error,
)
from libs.research_workflow.ports.source_catalog_projection_store_port import (
    SourceCatalogProjectionStorePort,
)
from libs.research_workflow.ports.workflow_job_store_port import WorkflowJobStorePort
from libs.scholarly_catalog.ports.project_arxiv_observation_port import (
    ProjectArxivObservationPort,
)
from libs.scholarly_catalog.ports.project_pubmed_observation_port import (
    ProjectPubmedObservationPort,
)


class ProjectSourceCatalogUnit:
    _SOURCES = frozenset({"arxiv", "pubmed"})
    _MAX_EXPLANATION_CANDIDATES_PER_UNIT = 2

    def __init__(
        self,
        observations: ListSourceObservationsPort,
        store: SourceCatalogProjectionStorePort,
        arxiv_replay: ReplayArxivObservationPort,
        arxiv_project: ProjectArxivObservationPort,
        pubmed_replay: ReplayPubmedObservationPort,
        pubmed_project: ProjectPubmedObservationPort,
        unit_context: ReadHarvestUnitContextPort | None = None,
        jobs: WorkflowJobStorePort | None = None,
    ) -> None:
        self._observations = observations
        self._store = store
        self._arxiv_replay = arxiv_replay
        self._arxiv_project = arxiv_project
        self._pubmed_replay = pubmed_replay
        self._pubmed_project = pubmed_project
        if (unit_context is None) != (jobs is None):
            raise Error("source_catalog_explanation_composition_incomplete")
        self._unit_context = unit_context
        self._jobs = jobs

    @staticmethod
    def _canonical(value: object) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
            raise Error("invalid_source_catalog_explanation_job") from exc

    def _enqueue_explanation(
        self,
        context,
        replay,
        result,
        scheduled_at: datetime,
    ) -> None:
        if self._unit_context is None or self._jobs is None:
            return
        state = getattr(result, "evidence_state", None)
        snapshot_id = getattr(result, "evidence_snapshot_id", None)
        if state == "unavailable":
            if snapshot_id is not None:
                raise Error("source_catalog_projection_result_mismatch")
            return
        if state != "available" or not isinstance(snapshot_id, str):
            raise Error("source_catalog_projection_result_mismatch")
        revision_id = getattr(result, "revision_id", None)
        work_id = getattr(result, "work_id", None)
        observation_id = getattr(result, "observation_id", None)
        if (
            not isinstance(revision_id, str)
            or not isinstance(work_id, str)
            or not isinstance(observation_id, str)
            or not isinstance(scheduled_at, datetime)
            or scheduled_at.tzinfo is None
            or scheduled_at.utcoffset() is None
        ):
            raise Error("source_catalog_projection_result_mismatch")
        payload = {
            "snapshot_id": snapshot_id,
            "revision_id": revision_id,
            "work_id": work_id,
            "profile_id": context.profile_id,
            "profile_revision": context.profile_revision,
            "domain_id": context.domain_id,
            "domain_revision": context.domain_revision,
        }
        encoded = self._canonical(payload)
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        business_key = (
            "explain:"
            + observation_id
            + ":"
            + context.profile_id
            + ":"
            + str(context.profile_revision)
            + ":"
            + context.domain_id
            + ":"
            + str(context.domain_revision)
        )
        self._jobs.enqueue(
            EnqueueWorkflowJob(
                job_kind="explain_snapshot",
                business_key=business_key,
                input_json=encoded,
                input_fingerprint=fingerprint,
                due_at=scheduled_at,
                created_at=scheduled_at,
            )
        )

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

        context = None
        if self._unit_context is not None:
            context = self._unit_context(unit_id, source)
            if context.unit_id != unit_id or context.source != source:
                raise Error("source_catalog_projection_context_mismatch")

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
            if (
                context is not None
                and progress.projected_count
                < self._MAX_EXPLANATION_CANDIDATES_PER_UNIT
            ):
                self._enqueue_explanation(
                    context,
                    replay,
                    result,
                    projected_at,
                )
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
