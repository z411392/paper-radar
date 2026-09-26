from dataclasses import dataclass


@dataclass(frozen=True)
class SearchLexicalQuery:
    text: str
    maximum_results: int = 20


@dataclass(frozen=True)
class SearchLexicalPlan:
    text: str
    mode: str
    maximum_results: int
    match_expression: str | None
    like_pattern: str | None


@dataclass(frozen=True)
class SearchLexicalHit:
    document_id: str
    work_id: str
    revision_id: str
    projection_kind: str


@dataclass(frozen=True)
class SearchLexicalResult:
    mode: str
    hits: tuple[SearchLexicalHit, ...]
