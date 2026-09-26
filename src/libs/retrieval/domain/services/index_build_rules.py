import hashlib
import math
import re

from libs.retrieval.dtos.index_build import (
    BuiltIndex,
    IndexBuildRequest,
    IndexBuildVector,
)
from libs.retrieval.dtos.index_generation import PreparedIndexGeneration
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError


class IndexBuildRules:
    _GENERATION = re.compile(r"faissgen:[0-9a-f]{64}")

    @classmethod
    def request(
        cls,
        generation: PreparedIndexGeneration,
        vectors: tuple[IndexBuildVector, ...],
    ) -> IndexBuildRequest:
        if (
            not isinstance(generation, PreparedIndexGeneration)
            or not isinstance(vectors, tuple)
            or len(vectors) != generation.vector_count
        ):
            raise IndexGenerationError("invalid_index_build")
        expected_ids = tuple(member.embedding_id for member in generation.members)
        actual_ids = tuple(vector.embedding_id for vector in vectors)
        if actual_ids != expected_ids:
            raise IndexGenerationError("index_vector_mapping_mismatch")
        for vector in vectors:
            if (
                not isinstance(vector, IndexBuildVector)
                or type(vector.embedding_id) is not int
                or vector.embedding_id < 1
                or not isinstance(vector.values, tuple)
                or len(vector.values) != generation.dimension
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in vector.values
                )
                or not any(float(value) != 0.0 for value in vector.values)
            ):
                raise IndexGenerationError("invalid_index_vector")
        return IndexBuildRequest(
            generation.generation_id,
            generation.dimension,
            generation.metric,
            vectors,
        )

    @classmethod
    def validate_request(
        cls,
        generation: PreparedIndexGeneration,
        request: IndexBuildRequest,
    ) -> None:
        if not isinstance(request, IndexBuildRequest):
            raise IndexGenerationError("invalid_index_build")
        rebuilt = cls.request(generation, request.vectors)
        if rebuilt != request:
            raise IndexGenerationError("invalid_index_build")

    @classmethod
    def validate_result(
        cls,
        request: IndexBuildRequest,
        result: BuiltIndex,
    ) -> str:
        if (
            not isinstance(result, BuiltIndex)
            or cls._GENERATION.fullmatch(result.generation_id) is None
            or result.generation_id != request.generation_id
            or type(result.vector_count) is not int
            or result.vector_count != len(request.vectors)
            or not isinstance(result.content_bytes, bytes)
            or not result.content_bytes
        ):
            raise IndexGenerationError("invalid_index_build_result")
        return hashlib.sha256(result.content_bytes).hexdigest()
