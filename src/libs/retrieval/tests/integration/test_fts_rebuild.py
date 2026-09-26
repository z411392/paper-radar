from pathlib import Path

import pytest

from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.application.queries.read_object import ReadObject
from libs.retrieval.adapters.driven.kernel_search_document_read_adapter import (
    KernelSearchDocumentReadAdapter,
)
from libs.retrieval.adapters.driven.sqlite_fts5_search_adapter import (
    SqliteFts5SearchAdapter,
)
from libs.retrieval.adapters.driven.sqlite_search_document_store_adapter import (
    SqliteSearchDocumentStoreAdapter,
)
from libs.retrieval.application.commands.rebuild_search_projection import (
    RebuildSearchProjection,
)
from libs.retrieval.exceptions.search_projection_error import SearchProjectionError
from libs.retrieval.tests.integration.test_search_document_persistence import (
    REVISION_2,
    _document,
    _workspace,
)


def _rebuild(root: Path, raw: SqliteConnectionFactory):
    from libs.kernel.adapters.driven.bundled_workspace_migrations import (
        load_workspace_migrations,
    )

    schema = SqliteSchemaConnectionFactory(
        root,
        load_workspace_migrations(with_runtime=True),
        minimum_version=25,
    )
    store = SqliteSearchDocumentStoreAdapter(schema.connect)
    reader = KernelSearchDocumentReadAdapter(
        ReadObject(
            FilesystemObjectBytesAdapter(root),
            SqliteObjectUnitOfWorkAdapter(raw),
        )
    )
    return RebuildSearchProjection(
        store,
        reader,
        SqliteFts5SearchAdapter(schema.connect),
    )


def _fts_ids(raw: SqliteConnectionFactory, table: str) -> tuple[str, ...]:
    connection = raw.connect()
    try:
        rows = connection.execute(
            f"SELECT document_id FROM {table} ORDER BY document_id"
        ).fetchall()
        return tuple(row[0] for row in rows)
    finally:
        connection.close()


def test_rebuild_indexes_only_current_documents_in_both_fts_tables(
    tmp_path: Path,
) -> None:
    root, raw, prepare = _workspace(tmp_path)
    stale = prepare(_document(explanation="舊版穩健統計說明。"))
    current = prepare(
        _document(
            revision_id=REVISION_2,
            abstract="Revised robust statistics abstract.",
            explanation="新版穩健統計方法與結果。",
        )
    )

    result = _rebuild(root, raw)()

    assert result.document_count == 1
    assert result.document_ids == (current.document_id,)
    assert stale.document_id != current.document_id
    assert _fts_ids(raw, "search_documents_fts") == (current.document_id,)
    assert _fts_ids(raw, "search_documents_fts_trigram") == (current.document_id,)

    connection = raw.connect()
    try:
        english = connection.execute(
            "SELECT document_id FROM search_documents_fts "
            "WHERE search_documents_fts MATCH ?",
            ("revised",),
        ).fetchall()
        chinese = connection.execute(
            "SELECT document_id FROM search_documents_fts_trigram "
            "WHERE search_documents_fts_trigram MATCH ?",
            ("穩健統計",),
        ).fetchall()
        assert [row[0] for row in english] == [current.document_id]
        assert [row[0] for row in chinese] == [current.document_id]
    finally:
        connection.close()


def test_exact_rebuild_is_idempotent(tmp_path: Path) -> None:
    root, raw, prepare = _workspace(tmp_path)
    current = prepare(_document())
    rebuild = _rebuild(root, raw)

    first = rebuild()
    second = rebuild()

    assert first == second
    assert first.document_ids == (current.document_id,)
    assert _fts_ids(raw, "search_documents_fts") == (current.document_id,)
    assert _fts_ids(raw, "search_documents_fts_trigram") == (current.document_id,)


def test_corrupt_current_object_does_not_destroy_previous_fts_projection(
    tmp_path: Path,
) -> None:
    root, raw, prepare = _workspace(tmp_path)
    current = prepare(_document())
    rebuild = _rebuild(root, raw)
    rebuild()
    before_unicode = _fts_ids(raw, "search_documents_fts")
    before_trigram = _fts_ids(raw, "search_documents_fts_trigram")

    connection = raw.connect()
    try:
        connection.execute(
            "UPDATE object_registry SET state='quarantined' WHERE object_id=?",
            (current.text_object_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception):
        rebuild()

    assert _fts_ids(raw, "search_documents_fts") == before_unicode
    assert _fts_ids(raw, "search_documents_fts_trigram") == before_trigram


def test_rebuild_limit_fails_before_replacing_existing_projection(
    tmp_path: Path,
) -> None:
    root, raw, prepare = _workspace(tmp_path)
    first = prepare(_document())
    rebuild = _rebuild(root, raw)
    rebuild()

    second = prepare(
        _document(
            projection_kind="paper_abstract",
            explanation="另一個獨立 projection。",
        )
    )
    assert second.document_id != first.document_id

    with pytest.raises(
        SearchProjectionError,
        match="search_projection_limit_exceeded",
    ):
        rebuild(maximum_documents=1)

    assert _fts_ids(raw, "search_documents_fts") == (first.document_id,)
    assert _fts_ids(raw, "search_documents_fts_trigram") == (first.document_id,)


def test_missing_current_object_registry_row_fails_without_replacing_fts(
    tmp_path: Path,
) -> None:
    root, raw, prepare = _workspace(tmp_path)
    current = prepare(_document())
    rebuild = _rebuild(root, raw)
    rebuild()
    before_unicode = _fts_ids(raw, "search_documents_fts")
    before_trigram = _fts_ids(raw, "search_documents_fts_trigram")

    connection = raw.connect()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "DELETE FROM object_registry WHERE object_id=?",
            (current.text_object_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception):
        rebuild()

    assert _fts_ids(raw, "search_documents_fts") == before_unicode
    assert _fts_ids(raw, "search_documents_fts_trigram") == before_trigram
