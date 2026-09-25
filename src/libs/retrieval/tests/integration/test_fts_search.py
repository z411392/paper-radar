from pathlib import Path

import pytest

from libs.retrieval.adapters.driven.sqlite_fts5_search_adapter import (
    SqliteFts5SearchAdapter,
)
from libs.retrieval.application.queries.search_lexical_documents import (
    SearchLexicalDocuments,
)
from libs.retrieval.dtos.search_query import SearchLexicalQuery
from libs.retrieval.exceptions.search_query_error import SearchQueryError
from libs.retrieval.tests.integration.test_fts_rebuild import _rebuild
from libs.retrieval.tests.integration.test_search_document_persistence import (
    REVISION_2,
    _document,
    _workspace,
)


def _search(root: Path, raw):
    from libs.kernel.adapters.driven.bundled_workspace_migrations import (
        load_workspace_migrations,
    )
    from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
        SqliteSchemaConnectionFactory,
    )

    schema = SqliteSchemaConnectionFactory(
        root,
        load_workspace_migrations(with_runtime=True),
        minimum_version=25,
    )
    return SearchLexicalDocuments(SqliteFts5SearchAdapter(schema.connect))


def _current_document(tmp_path: Path):
    root, raw, prepare = _workspace(tmp_path)
    document = prepare(
        _document(
            revision_id=REVISION_2,
            title="Robust statistics for clinical data",
            abstract="A revised robust estimation method.",
            explanation="這篇研究介紹穩健統計方法，並比較不同估計量。",
        )
    )
    _rebuild(root, raw)()
    return root, raw, document


def test_english_query_uses_unicode61_match(tmp_path: Path) -> None:
    root, raw, document = _current_document(tmp_path)

    result = _search(root, raw)(SearchLexicalQuery("robust", 20))

    assert result.mode == "unicode61_match"
    assert [hit.document_id for hit in result.hits] == [document.document_id]
    assert result.hits[0].revision_id == REVISION_2


def test_three_plus_character_chinese_query_uses_trigram_match(
    tmp_path: Path,
) -> None:
    root, raw, document = _current_document(tmp_path)

    result = _search(root, raw)(SearchLexicalQuery("穩健統計", 20))

    assert result.mode == "trigram_match"
    assert [hit.document_id for hit in result.hits] == [document.document_id]


def test_two_character_chinese_query_uses_explicit_like_fallback(
    tmp_path: Path,
) -> None:
    root, raw, document = _current_document(tmp_path)

    result = _search(root, raw)(SearchLexicalQuery("統計", 20))

    assert result.mode == "trigram_like_fallback"
    assert [hit.document_id for hit in result.hits] == [document.document_id]


def test_stale_fts_row_is_filtered_by_current_search_document_authority(
    tmp_path: Path,
) -> None:
    root, raw, prepare = _workspace(tmp_path)
    stale = prepare(
        _document(
            explanation="舊版貝葉斯方法。",
        )
    )
    current = prepare(
        _document(
            revision_id=REVISION_2,
            explanation="新版穩健統計方法。",
        )
    )
    _rebuild(root, raw)()

    connection = raw.connect()
    try:
        connection.execute(
            "INSERT INTO search_documents_fts_trigram("
            "document_id,title,abstract,explanation"
            ") VALUES(?,?,?,?)",
            (stale.document_id, "stale", "", "舊版貝葉斯方法。"),
        )
        connection.commit()
    finally:
        connection.close()

    result = _search(root, raw)(SearchLexicalQuery("舊版", 20))

    assert result.mode == "trigram_like_fallback"
    assert result.hits == ()
    assert stale.document_id != current.document_id


def test_query_limit_is_applied_after_current_filtering(tmp_path: Path) -> None:
    root, raw, prepare = _workspace(tmp_path)
    first = prepare(
        _document(
            projection_kind="paper_abstract",
            title="Robust statistics alpha",
        )
    )
    second = prepare(
        _document(
            projection_kind="paper_explanation",
            title="Robust statistics beta",
        )
    )
    _rebuild(root, raw)()

    result = _search(root, raw)(SearchLexicalQuery("robust", 1))

    assert len(result.hits) == 1
    assert result.hits[0].document_id in {first.document_id, second.document_id}


@pytest.mark.parametrize(
    "query",
    [
        SearchLexicalQuery("", 20),
        SearchLexicalQuery("   ", 20),
        SearchLexicalQuery("bad\0query", 20),
        SearchLexicalQuery("x" * 4097, 20),
        SearchLexicalQuery("robust", 0),
        SearchLexicalQuery("robust", 101),
    ],
)
def test_invalid_query_fails_without_touching_projection(
    tmp_path: Path,
    query: SearchLexicalQuery,
) -> None:
    root, raw, document = _current_document(tmp_path)

    with pytest.raises(SearchQueryError):
        _search(root, raw)(query)

    connection = raw.connect()
    try:
        rows = connection.execute(
            "SELECT document_id FROM search_documents_fts ORDER BY document_id"
        ).fetchall()
    finally:
        connection.close()
    assert [row[0] for row in rows] == [document.document_id]


def test_mixed_query_uses_trigram_when_full_literal_has_three_plus_characters(
    tmp_path: Path,
) -> None:
    root, raw, prepare = _workspace(tmp_path)
    document = prepare(
        _document(
            revision_id=REVISION_2,
            title="robust 統計 methods",
            explanation="mixed lexical fixture",
        )
    )
    _rebuild(root, raw)()

    result = _search(root, raw)(SearchLexicalQuery("robust 統計", 20))

    assert result.mode == "trigram_match"
    assert [hit.document_id for hit in result.hits] == [document.document_id]
