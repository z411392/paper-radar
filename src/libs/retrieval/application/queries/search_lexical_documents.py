from libs.retrieval.domain.services.search_query_rules import SearchQueryRules
from libs.retrieval.dtos.search_query import (
    SearchLexicalQuery,
    SearchLexicalResult,
)
from libs.retrieval.ports.search_lexical_index_port import SearchLexicalIndexPort


class SearchLexicalDocuments:
    def __init__(self, index: SearchLexicalIndexPort) -> None:
        self._index = index

    def __call__(self, query: SearchLexicalQuery) -> SearchLexicalResult:
        plan = SearchQueryRules.prepare(query)
        hits = self._index.search(plan)
        return SearchLexicalResult(plan.mode, hits)
