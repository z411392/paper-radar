import pytest

from libs.retrieval.domain.services.hybrid_rank_rules import HybridRankRules
from libs.retrieval.dtos.search_hybrid import SearchResolvedSemanticHit
from libs.retrieval.dtos.search_query import SearchLexicalHit
from libs.retrieval.exceptions.search_query_error import SearchQueryError


def lexical(document_id: str):
    return SearchLexicalHit(
        document_id,
        "work:" + document_id,
        "revision:" + document_id,
        "paper_abstract",
    )


def semantic(embedding_id: int, document_id: str, score: float):
    return SearchResolvedSemanticHit(
        embedding_id,
        document_id,
        "work:" + document_id,
        "revision:" + document_id,
        "paper_abstract",
        score,
    )


def test_rrf_fuses_rankings_without_cross_scale_score_math() -> None:
    result = HybridRankRules.fuse(
        (lexical("a"), lexical("b")),
        (semantic(2, "b", 0.91), semantic(3, "c", 0.72)),
        maximum_results=3,
        semantic_complete=True,
    )

    assert [hit.document_id for hit in result.hits] == ["b", "a", "c"]
    assert result.hits[0].lexical_rank == 2
    assert result.hits[0].semantic_rank == 1
    assert result.mode == "hybrid"
    assert result.degraded_mode is None
    assert result.incomplete_candidates is False


def test_lexical_only_is_explicit_degraded_mode() -> None:
    result = HybridRankRules.fuse(
        (lexical("a"), lexical("b")),
        (),
        maximum_results=2,
        semantic_complete=False,
        degraded_mode="lexical_only",
    )

    assert result.mode == "lexical"
    assert result.degraded_mode == "lexical_only"
    assert result.incomplete_candidates is False
    assert [hit.document_id for hit in result.hits] == ["a", "b"]


def test_semantic_incomplete_is_not_hidden() -> None:
    result = HybridRankRules.fuse(
        (lexical("a"),),
        (semantic(2, "b", 0.5),),
        maximum_results=2,
        semantic_complete=False,
    )

    assert result.mode == "hybrid"
    assert result.incomplete_candidates is True


def test_same_document_metadata_must_agree_across_retrievers() -> None:
    bad = SearchResolvedSemanticHit(
        1,
        "a",
        "work:other",
        "revision:a",
        "paper_abstract",
        0.5,
    )

    with pytest.raises(
        SearchQueryError,
        match="hybrid_candidate_identity_conflict",
    ):
        HybridRankRules.fuse(
            (lexical("a"),),
            (bad,),
            maximum_results=1,
            semantic_complete=True,
        )


def test_duplicate_semantic_document_is_rejected() -> None:
    with pytest.raises(SearchQueryError, match="invalid_hybrid_ranking"):
        HybridRankRules.fuse(
            (),
            (
                semantic(1, "a", 0.9),
                semantic(2, "a", 0.8),
            ),
            maximum_results=2,
            semantic_complete=True,
        )
