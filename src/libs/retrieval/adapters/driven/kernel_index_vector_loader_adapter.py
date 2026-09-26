import hashlib

from libs.kernel.ports.read_object_port import ReadObjectPort
from libs.retrieval.domain.services.index_build_rules import IndexBuildRules
from libs.retrieval.domain.services.index_generation_rules import IndexGenerationRules
from libs.retrieval.domain.services.npy_float32_batch_codec import (
    NpyFloat32BatchCodec,
)
from libs.retrieval.dtos.index_build import IndexBuildRequest, IndexBuildVector
from libs.retrieval.dtos.index_generation import PreparedIndexGeneration
from libs.retrieval.exceptions.embedding_batch_error import EmbeddingBatchError
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError


class KernelIndexVectorLoaderAdapter:
    def __init__(self, read_object: ReadObjectPort) -> None:
        self._read_object = read_object

    def load(
        self,
        generation: PreparedIndexGeneration,
    ) -> IndexBuildRequest:
        IndexGenerationRules.validate_prepared(generation)
        batches: dict[str, tuple[tuple[float, ...], ...]] = {}
        vectors = []
        for member in generation.members:
            rows = batches.get(member.object_id)
            if rows is None:
                try:
                    content = self._read_object(member.object_id)
                except Exception as exc:
                    raise IndexGenerationError(
                        "embedding_object_unavailable"
                    ) from exc
                digest = member.object_id.removeprefix("embedding:")
                if hashlib.sha256(content).hexdigest() != digest:
                    raise IndexGenerationError("embedding_object_corrupt")
                try:
                    rows = NpyFloat32BatchCodec.decode(content)
                except EmbeddingBatchError as exc:
                    raise IndexGenerationError(
                        "embedding_object_corrupt"
                    ) from exc
                if any(len(row) != generation.dimension for row in rows):
                    raise IndexGenerationError("embedding_dimension_mismatch")
                batches[member.object_id] = rows
            if member.row_offset >= len(rows):
                raise IndexGenerationError("embedding_row_offset_invalid")
            vectors.append(
                IndexBuildVector(
                    member.embedding_id,
                    rows[member.row_offset],
                )
            )
        return IndexBuildRules.request(generation, tuple(vectors))
