from typing import Protocol

from libs.retrieval.dtos.search_query import (
    SearchLexicalHit,
    SearchLexicalPlan,
)


class SearchLexicalIndexPort(Protocol):
    def search(self, plan: SearchLexicalPlan) -> tuple[SearchLexicalHit, ...]: ...
