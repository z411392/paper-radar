from dataclasses import replace

import pytest

from libs.retrieval.application.queries.search_papers import SearchPapers
from libs.retrieval.dtos.active_index import ActiveIndexPin
from libs.retrieval.dtos.search_hybrid import (
    SearchHybridQuery,
    SearchResolvedSemanticHit,
    SearchSemanticBatch,
    SearchSemanticHit,
)
from libs.retrieval.dtos.search_query import SearchLexicalHit
from libs.retrieval.exceptions.search_query_error import SearchQueryError


def pin():
    from datetime import datetime, timezone

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
        2,
        datetime(2026, 9, 26, tzinfo=timezone.utc),
    )


def lexical(document_id):
    return SearchLexicalHit(
        document_id,
        "work:" + document_id,
        "revision:" + document_id,
        "paper_abstract",
    )


class Lexical:
    def search(self, plan):
        assert plan.maximum_results >= 2
        return (lexical("a"), lexical("b"))


class Active:
    def __init__(self, value):
        self.value = value

    def pin(self, space_id):
        assert space_id == "embspace:" + "a" * 64
        return self.value


class Embeddings:
    def __init__(self):
        self.calls = 0

    def embed(self, active, text):
        self.calls += 1
        assert active == pin()
        assert text == "query"
        return (1.0, 0.0, 0.0, 0.0)


class Semantic:
    def __init__(self, batches):
        self.batches = iter(batches)
        self.limits = []

    def search(self, active, query_vector, *, maximum_candidates):
        assert active == pin()
        assert query_vector == (1.0, 0.0, 0.0, 0.0)
        self.limits.append(maximum_candidates)
        return next(self.batches)


class Resolver:
    def __init__(self, results):
        self.results = iter(results)

    def resolve(self, active, hits):
        assert active == pin()
        del hits
        return next(self.results)


class NoCall:
    def __getattr__(self, name):
        raise AssertionError(name + " must not be called")


def resolved(embedding_id, document_id, score):
    return SearchResolvedSemanticHit(
        embedding_id,
        document_id,
        "work:" + document_id,
        "revision:" + document_id,
        "paper_abstract",
        score,
    )


def test_missing_active_index_degrades_explicitly_to_lexical() -> None:
    result = SearchPapers(
        Lexical(),
        Active(None),
        NoCall(),
        NoCall(),
        NoCall(),
    )(
        SearchHybridQuery(
            "query",
            "embspace:" + "a" * 64,
            maximum_results=2,
            rank_window_size=2,
            maximum_semantic_candidates=4,
        )
    )

    assert result.mode == "lexical"
    assert result.degraded_mode == "lexical_only"
    assert [hit.document_id for hit in result.hits] == ["a", "b"]


def test_semantic_candidates_expand_after_sqlite_current_filtering() -> None:
    semantic = Semantic(
        (
            SearchSemanticBatch(
                pin().generation_id,
                2,
                False,
                (
                    SearchSemanticHit(1, 0.9),
                    SearchSemanticHit(2, 0.8),
                ),
            ),
            SearchSemanticBatch(
                pin().generation_id,
                4,
                True,
                (
                    SearchSemanticHit(1, 0.9),
                    SearchSemanticHit(2, 0.8),
                    SearchSemanticHit(3, 0.7),
                ),
            ),
        )
    )
    resolver = Resolver(
        (
            (resolved(1, "b", 0.9),),
            (
                resolved(1, "b", 0.9),
                resolved(3, "c", 0.7),
            ),
        )
    )

    result = SearchPapers(
        Lexical(),
        Active(pin()),
        Embeddings(),
        semantic,
        resolver,
    )(
        SearchHybridQuery(
            "query",
            pin().space_id,
            maximum_results=2,
            rank_window_size=2,
            maximum_semantic_candidates=4,
        )
    )

    assert semantic.limits == [2, 4]
    assert result.incomplete_candidates is False
    assert result.mode == "hybrid"
    assert result.hits[0].document_id == "b"


def test_candidate_cap_marks_incomplete_instead_of_fake_complete() -> None:
    semantic = Semantic(
        (
            SearchSemanticBatch(
                pin().generation_id,
                2,
                False,
                (SearchSemanticHit(1, 0.9),),
            ),
        )
    )

    result = SearchPapers(
        Lexical(),
        Active(pin()),
        Embeddings(),
        semantic,
        Resolver(((resolved(1, "b", 0.9),),)),
    )(
        SearchHybridQuery(
            "query",
            pin().space_id,
            maximum_results=2,
            rank_window_size=2,
            maximum_semantic_candidates=2,
        )
    )

    assert result.incomplete_candidates is True


def test_expanded_semantic_results_must_keep_previous_prefix() -> None:
    semantic = Semantic(
        (
            SearchSemanticBatch(
                pin().generation_id,
                2,
                False,
                (
                    SearchSemanticHit(1, 0.9),
                    SearchSemanticHit(2, 0.8),
                ),
            ),
            SearchSemanticBatch(
                pin().generation_id,
                4,
                True,
                (
                    SearchSemanticHit(2, 0.8),
                    SearchSemanticHit(1, 0.9),
                ),
            ),
        )
    )
    search = SearchPapers(
        Lexical(),
        Active(pin()),
        Embeddings(),
        semantic,
        Resolver(
            (
                (resolved(1, "b", 0.9),),
                (resolved(2, "c", 0.8),),
            )
        ),
    )

    with pytest.raises(SearchQueryError, match="semantic_search_rank_drift"):
        search(
            SearchHybridQuery(
                "query",
                pin().space_id,
                maximum_results=2,
                rank_window_size=2,
                maximum_semantic_candidates=4,
            )
        )


def test_invalid_query_embedding_is_not_silently_degraded() -> None:
    class BadEmbedding:
        def embed(self, active, text):
            del active, text
            return (float("nan"), 0.0, 0.0, 0.0)

    with pytest.raises(SearchQueryError, match="invalid_query_embedding"):
        SearchPapers(
            Lexical(),
            Active(pin()),
            BadEmbedding(),
            NoCall(),
            NoCall(),
        )(
            SearchHybridQuery(
                "query",
                pin().space_id,
                maximum_results=2,
                rank_window_size=2,
                maximum_semantic_candidates=4,
            )
        )
