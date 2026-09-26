from libs.retrieval.domain.services.index_build_rules import IndexBuildRules
from libs.retrieval.domain.services.index_generation_rules import (
    IndexGenerationRules,
)
from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.active_index_artifact import ActiveIndexArtifacts
from libs.retrieval.dtos.index_generation import IndexGenerationInput
from libs.retrieval.exceptions.active_index_error import ActiveIndexError
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.ports.active_index_artifact_reader_port import (
    ActiveIndexArtifactReaderPort,
)
from libs.retrieval.ports.active_index_artifact_repair_port import (
    ActiveIndexArtifactRepairPort,
)
from libs.retrieval.ports.index_builder_port import IndexBuilderPort
from libs.retrieval.ports.index_generation_store_port import (
    IndexGenerationStorePort,
)
from libs.retrieval.ports.index_vector_loader_port import IndexVectorLoaderPort


class RebuildActiveIndexArtifacts:
    def __init__(
        self,
        store: IndexGenerationStorePort,
        vectors: IndexVectorLoaderPort,
        builder: IndexBuilderPort,
        repair: ActiveIndexArtifactRepairPort,
        reader: ActiveIndexArtifactReaderPort,
    ) -> None:
        self._store = store
        self._vectors = vectors
        self._builder = builder
        self._repair = repair
        self._reader = reader

    @staticmethod
    def _matches(pin: ActiveIndexPin, generation) -> bool:
        return (
            generation.generation_id == pin.generation_id
            and generation.space_id == pin.space_id
            and generation.relative_directory == pin.relative_directory
            and generation.dimension == pin.dimension
            and generation.dtype == pin.dtype
            and generation.metric == pin.metric
            and generation.membership_digest == pin.membership_digest
            and generation.document_high_watermark
            == pin.document_high_watermark
            and generation.vector_count == pin.vector_count
        )

    def __call__(
        self,
        pin: ActiveIndexPin,
        config: IndexGenerationInput,
    ) -> ActiveIndexArtifacts:
        if (
            not isinstance(pin, ActiveIndexPin)
            or not isinstance(config, IndexGenerationInput)
            or config.space_id != pin.space_id
        ):
            raise ActiveIndexError(
                "active_index_rebuild_config_mismatch"
            )
        try:
            snapshot = self._store.snapshot_generation(pin.generation_id)
            generation = IndexGenerationRules.prepare(snapshot, config)
        except IndexGenerationError as exc:
            raise ActiveIndexError(
                "active_index_rebuild_source_invalid"
            ) from exc
        if not self._matches(pin, generation):
            raise ActiveIndexError(
                "active_index_rebuild_config_mismatch"
            )

        try:
            request = self._vectors.load(generation)
            IndexBuildRules.validate_request(generation, request)
            built = self._builder.build(request)
            index_sha256 = IndexBuildRules.validate_result(request, built)
            manifest = IndexGenerationRules.manifest(
                generation,
                index_sha256=index_sha256,
            )
        except IndexGenerationError as exc:
            raise ActiveIndexError("active_index_rebuild_failed") from exc
        if (
            index_sha256 != pin.index_sha256
            or manifest.manifest_sha256 != pin.manifest_sha256
        ):
            raise ActiveIndexError(
                "active_index_rebuild_hash_mismatch"
            )

        published = self._repair.repair(
            generation,
            index_bytes=built.content_bytes,
            manifest=manifest,
        )
        if (
            published.generation_id != pin.generation_id
            or published.relative_directory != pin.relative_directory
            or published.index_sha256 != pin.index_sha256
            or published.manifest_sha256 != pin.manifest_sha256
        ):
            raise ActiveIndexError("active_index_rebuild_hash_mismatch")

        result = self._reader.read(pin)
        expected_ids = tuple(
            member.embedding_id for member in generation.members
        )
        if result.embedding_ids != expected_ids:
            raise ActiveIndexError(
                "active_index_rebuild_mapping_mismatch"
            )
        return result
