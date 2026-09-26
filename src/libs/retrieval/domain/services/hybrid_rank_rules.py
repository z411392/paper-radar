import math

from libs.retrieval.dtos.search_hybrid import (
    SearchHybridHit,
    SearchHybridResult,
    SearchResolvedSemanticHit,
)
from libs.retrieval.dtos.search_query import SearchLexicalHit
from libs.retrieval.exceptions.search_query_error import SearchQueryError


class HybridRankRules:
    RRF_K = 60

    @staticmethod
    def _identity(hit) -> tuple[str, str, str, str]:
        return (
            hit.document_id,
            hit.work_id,
            hit.revision_id,
            hit.projection_kind,
        )

    @classmethod
    def fuse(
        cls,
        lexical: tuple[SearchLexicalHit, ...],
        semantic: tuple[SearchResolvedSemanticHit, ...],
        *,
        maximum_results: int,
        semantic_complete: bool,
        degraded_mode: str | None = None,
    ) -> SearchHybridResult:
        if (
            not isinstance(lexical, tuple)
            or not isinstance(semantic, tuple)
            or type(maximum_results) is not int
            or not 1 <= maximum_results <= 100
            or type(semantic_complete) is not bool
            or degraded_mode not in {None, "lexical_only"}
            or (degraded_mode == "lexical_only" and semantic)
        ):
            raise SearchQueryError("invalid_hybrid_ranking")

        rows: dict[str, dict[str, object]] = {}
        for rank, hit in enumerate(lexical, start=1):
            if (
                not isinstance(hit, SearchLexicalHit)
                or not all(
                    isinstance(value, str) and value
                    for value in cls._identity(hit)
                )
                or hit.document_id in rows
            ):
                raise SearchQueryError("invalid_hybrid_ranking")
            rows[hit.document_id] = {
                "identity": cls._identity(hit),
                "lexical_rank": rank,
                "semantic_rank": None,
                "score": 1.0 / (cls.RRF_K + rank),
            }

        semantic_seen = set()
        for rank, hit in enumerate(semantic, start=1):
            if (
                not isinstance(hit, SearchResolvedSemanticHit)
                or type(hit.embedding_id) is not int
                or hit.embedding_id < 1
                or not all(
                    isinstance(value, str) and value
                    for value in cls._identity(hit)
                )
                or not isinstance(hit.raw_score, (int, float))
                or isinstance(hit.raw_score, bool)
                or not math.isfinite(float(hit.raw_score))
                or hit.embedding_id in semantic_seen
            ):
                raise SearchQueryError("invalid_hybrid_ranking")
            semantic_seen.add(hit.embedding_id)
            existing = rows.get(hit.document_id)
            contribution = 1.0 / (cls.RRF_K + rank)
            if existing is None:
                rows[hit.document_id] = {
                    "identity": cls._identity(hit),
                    "lexical_rank": None,
                    "semantic_rank": rank,
                    "score": contribution,
                }
                continue
            if existing["identity"] != cls._identity(hit):
                raise SearchQueryError("hybrid_candidate_identity_conflict")
            if existing["semantic_rank"] is not None:
                raise SearchQueryError("invalid_hybrid_ranking")
            existing["semantic_rank"] = rank
            existing["score"] = float(existing["score"]) + contribution

        def sort_key(item):
            document_id, row = item
            lexical_rank = row["lexical_rank"]
            semantic_rank = row["semantic_rank"]
            best_rank = min(
                rank
                for rank in (lexical_rank, semantic_rank)
                if rank is not None
            )
            return (
                -float(row["score"]),
                best_rank,
                lexical_rank if lexical_rank is not None else 1_000_000,
                semantic_rank if semantic_rank is not None else 1_000_000,
                document_id,
            )

        ordered = sorted(rows.items(), key=sort_key)
        hits = tuple(
            SearchHybridHit(
                row["identity"][0],
                row["identity"][1],
                row["identity"][2],
                row["identity"][3],
                float(row["score"]),
                row["lexical_rank"],
                row["semantic_rank"],
            )
            for _, row in ordered[:maximum_results]
        )
        return SearchHybridResult(
            "lexical" if degraded_mode == "lexical_only" else "hybrid",
            degraded_mode,
            False if degraded_mode == "lexical_only" else not semantic_complete,
            hits,
        )
