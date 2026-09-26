from datetime import datetime, timezone

import pytest

from libs.retrieval.application.queries.search_papers import SearchPapers
from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.active_index_artifact import ActiveIndexArtifacts
from libs.retrieval.dtos.search_hybrid import (
    SearchHybridQuery,
    SearchResolvedSemanticHit,
    SearchSemanticBatch,
    SearchSemanticHit,
)
from libs.retrieval.dtos.search_query import SearchLexicalHit
from libs.retrieval.exceptions.active_index_error import ActiveIndexError
from libs.retrieval.exceptions.search_query_error import SearchQueryError


def _pin():
    return ActiveIndexPin(
        "embspace:" + "a" * 64,
        "faissgen:" + "b" * 64,
        1,
        "derived/faiss/a/b",
        4,
        "float32",
        "inner_product",
        "c" * 64,
        "d" * 64,
        "e" * 64,
        2,
        1,
        datetime(2026, 9, 26, tzinfo=timezone.utc),
    )


class Lexical:
    def search(self, plan):
        del plan
        return (
            SearchLexicalHit(
                "doc:a",
                "work:a",
                "revision:a",
                "paper_abstract",
            ),
        )


class Active:
    def pin(self, space_id):
        assert space_id == _pin().space_id
        return _pin()


class BrokenArtifacts:
    def read(self, pin):
        assert pin == _pin()
        raise ActiveIndexError("active_index_artifact_corrupt")


class Artifacts:
    def __init__(self):
        self.value = ActiveIndexArtifacts(
            _pin(),
            b"index",
            b"{}",
            (1,),
        )

    def read(self, pin):
        assert pin == _pin()
        return self.value


class Embeddings:
    def embed(self, pin, text):
        assert pin == _pin()
        assert text == "query"
        return (1.0, 0.0, 0.0, 0.0)


class Semantic:
    def __init__(self, expected):
        self.expected = expected
        self.seen = None

    def search(self, artifacts, query_vector, *, maximum_candidates):
        self.seen = artifacts
        assert artifacts is self.expected
        assert query_vector == (1.0, 0.0, 0.0, 0.0)
        return SearchSemanticBatch(
            artifacts.pin.generation_id,
            maximum_candidates,
            True,
            (SearchSemanticHit(1, 0.9),),
        )


class Resolver:
    def resolve(self, pin, hits):
        assert pin == _pin()
        assert hits == (SearchSemanticHit(1, 0.9),)
        return (
            SearchResolvedSemanticHit(
                1,
                "doc:a",
                "work:a",
                "revision:a",
                "paper_abstract",
                0.9,
            ),
        )


class NoCall:
    def __getattr__(self, name):
        raise AssertionError(name + " must not be called")


def _query():
    return SearchHybridQuery(
        "query",
        _pin().space_id,
        maximum_results=1,
        rank_window_size=1,
        maximum_semantic_candidates=2,
    )


def test_active_artifact_failure_never_silently_degrades_to_lexical() -> None:
    search = SearchPapers(
        Lexical(),
        Active(),
        BrokenArtifacts(),
        NoCall(),
        NoCall(),
        NoCall(),
    )

    with pytest.raises(
        SearchQueryError,
        match="semantic_index_artifact_invalid",
    ):
        search(_query())


def test_semantic_port_receives_the_verified_artifact_bundle() -> None:
    artifacts = Artifacts()
    semantic = Semantic(artifacts.value)
    search = SearchPapers(
        Lexical(),
        Active(),
        artifacts,
        Embeddings(),
        semantic,
        Resolver(),
    )

    result = search(_query())

    assert semantic.seen is artifacts.value
    assert result.degraded_mode is None
    assert result.mode == "hybrid"
