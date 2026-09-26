import pytest

from libs.retrieval.domain.services.index_build_rules import IndexBuildRules
from libs.retrieval.dtos.index_build import BuiltIndex, IndexBuildVector
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.tests.integration.test_index_generation_manifest import (
    _generation,
)
from libs.retrieval.domain.services.index_generation_rules import (
    IndexGenerationRules,
)


def _prepared(tmp_path):
    _, _, _, space, persisted, store, config = _generation(tmp_path)
    generation = IndexGenerationRules.prepare(
        store.snapshot(space.space_id),
        config,
    )
    values = {
        item.embedding_id: (
            (1.0, 0.0, 0.0, 0.0)
            if index == 0
            else (0.0, 1.0, 0.0, 0.0)
        )
        for index, item in enumerate(persisted.embeddings)
    }
    vectors = tuple(
        IndexBuildVector(
            member.embedding_id,
            values[member.embedding_id],
        )
        for member in generation.members
    )
    return generation, vectors


def test_build_request_preserves_stable_embedding_ids(tmp_path):
    generation, vectors = _prepared(tmp_path)

    request = IndexBuildRules.request(generation, vectors)

    assert tuple(item.embedding_id for item in request.vectors) == tuple(
        member.embedding_id for member in generation.members
    )
    assert request.dimension == generation.dimension
    assert request.metric == generation.metric


def test_build_request_rejects_mapping_reorder(tmp_path):
    generation, vectors = _prepared(tmp_path)

    with pytest.raises(
        IndexGenerationError,
        match="index_vector_mapping_mismatch",
    ):
        IndexBuildRules.request(generation, tuple(reversed(vectors)))


@pytest.mark.parametrize(
    "values",
    [
        (0.0, 0.0, 0.0, 0.0),
        (1.0, float("nan"), 0.0, 0.0),
        (1.0, 0.0),
    ],
)
def test_build_request_rejects_invalid_vector(tmp_path, values):
    generation, vectors = _prepared(tmp_path)
    changed = (
        IndexBuildVector(vectors[0].embedding_id, values),
        *vectors[1:],
    )

    with pytest.raises(IndexGenerationError, match="invalid_index_vector"):
        IndexBuildRules.request(generation, changed)


def test_build_result_must_match_generation_and_count(tmp_path):
    generation, vectors = _prepared(tmp_path)
    request = IndexBuildRules.request(generation, vectors)

    digest = IndexBuildRules.validate_result(
        request,
        BuiltIndex(
            generation.generation_id,
            generation.vector_count,
            b"faiss-bytes",
        ),
    )

    assert len(digest) == 64
