from dataclasses import dataclass


@dataclass(frozen=True)
class SearchSemanticHit:
    embedding_id: int
    raw_score: float


@dataclass(frozen=True)
class SearchResolvedSemanticHit:
    embedding_id: int
    document_id: str
    work_id: str
    revision_id: str
    projection_kind: str
    raw_score: float


@dataclass(frozen=True)
class SearchHybridHit:
    document_id: str
    work_id: str
    revision_id: str
    projection_kind: str
    rrf_score: float
    lexical_rank: int | None
    semantic_rank: int | None


@dataclass(frozen=True)
class SearchHybridResult:
    mode: str
    degraded_mode: str | None
    incomplete_candidates: bool
    hits: tuple[SearchHybridHit, ...]


@dataclass(frozen=True)
class SearchHybridQuery:
    text: str
    space_id: str
    maximum_results: int = 20
    rank_window_size: int = 50
    maximum_semantic_candidates: int = 100


@dataclass(frozen=True)
class SearchSemanticBatch:
    generation_id: str
    requested_candidates: int
    exhausted: bool
    hits: tuple[SearchSemanticHit, ...]
