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
