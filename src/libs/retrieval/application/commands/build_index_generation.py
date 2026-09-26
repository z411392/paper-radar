from collections.abc import Callable
from datetime import datetime, timezone

from libs.retrieval.domain.services.index_build_rules import IndexBuildRules
from libs.retrieval.domain.services.index_generation_rules import IndexGenerationRules
from libs.retrieval.dtos.index_generation import (
    IndexGenerationInput,
    PersistedIndexGeneration,
)
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.ports.index_builder_port import IndexBuilderPort
from libs.retrieval.ports.index_generation_artifact_store_port import (
    IndexGenerationArtifactStorePort,
)
from libs.retrieval.ports.index_generation_store_port import IndexGenerationStorePort
from libs.retrieval.ports.index_vector_loader_port import IndexVectorLoaderPort


class BuildIndexGeneration:
    def __init__(
        self,
        store: IndexGenerationStorePort,
        vectors: IndexVectorLoaderPort,
        builder: IndexBuilderPort,
        artifacts: IndexGenerationArtifactStorePort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self._vectors = vectors
        self._builder = builder
        self._artifacts = artifacts
        self._clock = clock

    def __call__(
        self,
        value: IndexGenerationInput,
    ) -> PersistedIndexGeneration:
        snapshot = self._store.snapshot(value.space_id)
        prepared = IndexGenerationRules.prepare(snapshot, value)
        started = self._store.start(
            prepared,
            created_at=IndexGenerationRules.instant(self._clock()),
        )
        if started.state == "failed":
            raise IndexGenerationError("index_generation_failed")

        request = self._vectors.load(prepared)
        IndexBuildRules.validate_request(prepared, request)
        built = self._builder.build(request)
        index_sha256 = IndexBuildRules.validate_result(request, built)
        manifest = IndexGenerationRules.manifest(
            prepared,
            index_sha256=index_sha256,
        )
        published = self._artifacts.publish(
            prepared,
            index_bytes=built.content_bytes,
            manifest=manifest,
        )
        if (
            published.generation_id != prepared.generation_id
            or published.relative_directory != prepared.relative_directory
            or published.index_sha256 != index_sha256
            or published.manifest_sha256 != manifest.manifest_sha256
        ):
            raise IndexGenerationError("index_artifact_mismatch")
        return self._store.mark_ready(
            prepared,
            manifest,
            index_sha256=index_sha256,
            verified_at=IndexGenerationRules.instant(self._clock()),
        )
