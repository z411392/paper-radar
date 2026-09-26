import math
import re

from libs.retrieval.domain.services.hybrid_rank_rules import HybridRankRules
from libs.retrieval.domain.services.search_query_rules import SearchQueryRules
from libs.retrieval.dtos.search_hybrid import (
    SearchHybridQuery,
    SearchHybridResult,
    SearchSemanticBatch,
)
from libs.retrieval.dtos.search_query import SearchLexicalQuery
from libs.retrieval.exceptions.search_query_error import SearchQueryError
from libs.retrieval.ports.active_index_store_port import ActiveIndexStorePort
from libs.retrieval.ports.search_lexical_index_port import SearchLexicalIndexPort
from libs.retrieval.ports.search_query_embedding_port import SearchQueryEmbeddingPort
from libs.retrieval.ports.search_semantic_candidate_resolver_port import (
    SearchSemanticCandidateResolverPort,
)
from libs.retrieval.ports.search_semantic_index_port import SearchSemanticIndexPort


class SearchPapers:
    _SPACE = re.compile(r"embspace:[0-9a-f]{64}")

    def __init__(
        self,
        lexical: SearchLexicalIndexPort,
        active_indexes: ActiveIndexStorePort,
        embeddings: SearchQueryEmbeddingPort,
        semantic: SearchSemanticIndexPort,
        resolver: SearchSemanticCandidateResolverPort,
    ) -> None:
        self._lexical = lexical
        self._active_indexes = active_indexes
        self._embeddings = embeddings
        self._semantic = semantic
        self._resolver = resolver

    @classmethod
    def _validate_query(cls, value: SearchHybridQuery) -> None:
        if (
            not isinstance(value, SearchHybridQuery)
            or not isinstance(value.text, str)
            or not value.text.strip()
            or "\0" in value.text
            or cls._SPACE.fullmatch(value.space_id) is None
            or type(value.maximum_results) is not int
            or not 1 <= value.maximum_results <= 100
            or type(value.rank_window_size) is not int
            or not value.maximum_results <= value.rank_window_size <= 100
            or type(value.maximum_semantic_candidates) is not int
            or not value.rank_window_size
            <= value.maximum_semantic_candidates
            <= 10_000
        ):
            raise SearchQueryError("invalid_hybrid_search_query")

    @staticmethod
    def _vector(pin, value) -> tuple[float, ...]:
        if (
            not isinstance(value, tuple)
            or len(value) != pin.dimension
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(float(item))
                for item in value
            )
            or not any(float(item) != 0.0 for item in value)
        ):
            raise SearchQueryError("invalid_query_embedding")
        return tuple(float(item) for item in value)

    @staticmethod
    def _batch(
        pin,
        value: SearchSemanticBatch,
        expected_limit: int,
    ) -> None:
        if (
            not isinstance(value, SearchSemanticBatch)
            or value.generation_id != pin.generation_id
            or type(value.requested_candidates) is not int
            or value.requested_candidates != expected_limit
            or type(value.exhausted) is not bool
            or not isinstance(value.hits, tuple)
            or len(value.hits) > expected_limit
        ):
            raise SearchQueryError("invalid_semantic_search_result")

    @staticmethod
    def _prefix(previous, current) -> None:
        if previous and current[: len(previous)] != previous:
            raise SearchQueryError("semantic_search_rank_drift")

    def __call__(self, value: SearchHybridQuery) -> SearchHybridResult:
        self._validate_query(value)
        lexical_plan = SearchQueryRules.prepare(
            SearchLexicalQuery(
                value.text,
                value.rank_window_size,
            )
        )
        lexical_hits = self._lexical.search(lexical_plan)
        pin = self._active_indexes.pin(value.space_id)
        if pin is None:
            return HybridRankRules.fuse(
                lexical_hits,
                (),
                maximum_results=value.maximum_results,
                semantic_complete=False,
                degraded_mode="lexical_only",
            )

        query_vector = self._vector(
            pin,
            self._embeddings.embed(pin, value.text),
        )
        limit = value.rank_window_size
        previous = ()
        resolved = ()
        semantic_complete = False
        while True:
            batch = self._semantic.search(
                pin,
                query_vector,
                maximum_candidates=limit,
            )
            self._batch(pin, batch, limit)
            self._prefix(previous, batch.hits)
            previous = batch.hits
            resolved = self._resolver.resolve(pin, batch.hits)
            if len(resolved) >= value.maximum_results or batch.exhausted:
                semantic_complete = True
                break
            if limit >= value.maximum_semantic_candidates:
                semantic_complete = False
                break
            limit = min(
                value.maximum_semantic_candidates,
                max(limit + 1, limit * 2),
            )

        return HybridRankRules.fuse(
            lexical_hits,
            resolved,
            maximum_results=value.maximum_results,
            semantic_complete=semantic_complete,
        )
