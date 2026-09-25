from libs.retrieval.dtos.search_query import (
    SearchLexicalPlan,
    SearchLexicalQuery,
)
from libs.retrieval.exceptions.search_query_error import SearchQueryError


class SearchQueryRules:
    MAX_QUERY_BYTES = 4096
    _MODES = frozenset(
        {
            "unicode61_match",
            "trigram_match",
            "trigram_like_fallback",
        }
    )

    @staticmethod
    def _is_cjk(character: str) -> bool:
        value = ord(character)
        return (
            0x3400 <= value <= 0x4DBF
            or 0x4E00 <= value <= 0x9FFF
            or 0xF900 <= value <= 0xFAFF
        )

    @staticmethod
    def _fts_literal(text: str) -> str:
        return '"' + text.replace('"', '""') + '"'

    @staticmethod
    def _like_literal(text: str) -> str:
        escaped = (
            text.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return "%" + escaped + "%"

    @classmethod
    def prepare(cls, value: SearchLexicalQuery) -> SearchLexicalPlan:
        if (
            not isinstance(value, SearchLexicalQuery)
            or not isinstance(value.text, str)
            or "\0" in value.text
            or not value.text.strip()
            or type(value.maximum_results) is not int
            or not 1 <= value.maximum_results <= 100
        ):
            raise SearchQueryError("invalid_search_query")
        try:
            size = len(value.text.encode("utf-8"))
        except UnicodeEncodeError:
            raise SearchQueryError("invalid_search_query") from None
        if size > cls.MAX_QUERY_BYTES:
            raise SearchQueryError("invalid_search_query")

        cjk_count = sum(1 for character in value.text if cls._is_cjk(character))
        if cjk_count == 0:
            mode = "unicode61_match"
            expression = cls._fts_literal(value.text)
            like_pattern = None
        elif cjk_count >= 3:
            mode = "trigram_match"
            expression = cls._fts_literal(value.text)
            like_pattern = None
        else:
            mode = "trigram_like_fallback"
            expression = None
            like_pattern = cls._like_literal(value.text)

        return SearchLexicalPlan(
            value.text,
            mode,
            value.maximum_results,
            expression,
            like_pattern,
        )

    @classmethod
    def validate_plan(cls, value: SearchLexicalPlan) -> None:
        if (
            not isinstance(value, SearchLexicalPlan)
            or value.mode not in cls._MODES
            or type(value.maximum_results) is not int
            or not 1 <= value.maximum_results <= 100
        ):
            raise SearchQueryError("invalid_search_query_plan")
        rebuilt = cls.prepare(
            SearchLexicalQuery(value.text, value.maximum_results)
        )
        if rebuilt != value:
            raise SearchQueryError("invalid_search_query_plan")
