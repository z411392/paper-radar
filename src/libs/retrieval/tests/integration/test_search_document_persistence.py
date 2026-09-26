from dataclasses import replace
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import load_workspace_migrations
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import FilesystemObjectBytesAdapter
from libs.kernel.adapters.driven.sqlite_connection_factory import SqliteConnectionFactory
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import SqliteSchemaConnectionFactory
from libs.kernel.adapters.driven.sqlite_workspace_bootstrap_adapter import (
    SqliteWorkspaceBootstrapAdapter,
)
from libs.kernel.application.commands.publish_object import PublishObject
from libs.retrieval.adapters.driven.kernel_search_document_artifact_adapter import (
    KernelSearchDocumentArtifactAdapter,
)
from libs.retrieval.adapters.driven.sqlite_search_document_store_adapter import (
    SqliteSearchDocumentStoreAdapter,
)
from libs.retrieval.application.commands.prepare_search_document import PrepareSearchDocument
from libs.retrieval.dtos.search_document import SearchDocumentInput
from libs.retrieval.exceptions.search_document_error import SearchDocumentError


WORK = "work:" + "1" * 64
MANIFESTATION = "manifestation:fixture"
REVISION_1 = "revision:" + "2" * 64
REVISION_2 = "revision:" + "3" * 64


def _workspace(tmp_path: Path):
    root = tmp_path / "runtime"
    migrations = load_workspace_migrations(with_runtime=True)
    SqliteWorkspaceBootstrapAdapter(root, migrations).initialize()
    raw = SqliteConnectionFactory(root)
    connection = raw.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO paper_works VALUES(?,?,?,?,?,?,?)",
            (
                WORK,
                "Canonical title",
                "published",
                "2026-09-24",
                "day",
                "2026-09-24T00:00:00+00:00",
                "2026-09-24T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO paper_manifestations VALUES(?,?,?,?,?,?,?)",
            (
                MANIFESTATION,
                WORK,
                "fixture",
                "native:1",
                "publication",
                "https://example.invalid/paper",
                "2026-09-24T00:00:00+00:00",
            ),
        )
        for revision, version, fingerprint in (
            (REVISION_1, "1", "a" * 64),
            (REVISION_2, "2", "b" * 64),
        ):
            connection.execute(
                "INSERT INTO paper_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    revision,
                    MANIFESTATION,
                    WORK,
                    version,
                    fingerprint,
                    "Canonical title",
                    None,
                    "2026-09-24T00:00:00+00:00",
                    "2026-09-24",
                    "day",
                    "2026-09-24T00:00:00+00:00",
                ),
            )
        connection.commit()
    finally:
        connection.close()

    schema = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=6,
    )
    publish = PublishObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(raw),
    )
    command = PrepareSearchDocument(
        KernelSearchDocumentArtifactAdapter(publish),
        SqliteSearchDocumentStoreAdapter(schema.connect),
    )
    return root, raw, command


def _document(**changes):
    values = {
        "work_id": WORK,
        "revision_id": REVISION_1,
        "projection_kind": "paper_reading",
        "title": "A paper about robust statistics",
        "abstract": "We study robust estimators.",
        "explanation": "這篇研究討論穩健統計估計。",
    }
    values.update(changes)
    return SearchDocumentInput(**values)


def test_exact_replay_reuses_document_sequence_and_object(tmp_path: Path) -> None:
    _, raw, command = _workspace(tmp_path)

    first = command(_document())
    replay = command(_document())

    assert first.document_id == replay.document_id
    assert first.input_fingerprint == replay.input_fingerprint
    assert first.text_object_id == replay.text_object_id
    assert first.sequence_no == replay.sequence_no == 1
    assert first.is_current is True
    assert replay.is_current is True
    assert first.replayed is False
    assert replay.replayed is True

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM search_documents"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM object_registry "
            "WHERE retention_policy='search-document-v1'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_new_fingerprint_atomically_replaces_current_document(tmp_path: Path) -> None:
    _, raw, command = _workspace(tmp_path)

    first = command(_document())
    second = command(
        _document(
            revision_id=REVISION_2,
            abstract="We study a revised robust estimator.",
        )
    )

    assert second.document_id != first.document_id
    assert second.sequence_no == 2
    assert second.is_current is True

    connection = raw.connect()
    try:
        rows = connection.execute(
            "SELECT id,is_current,sequence_no FROM search_documents "
            "ORDER BY sequence_no"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (first.document_id, 0, 1),
            (second.document_id, 1, 2),
        ]
        assert connection.execute(
            "SELECT count(*) FROM search_documents "
            "WHERE work_id=? AND projection_kind=? AND is_current=1",
            (WORK, "paper_reading"),
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_replaying_stale_fingerprint_never_rolls_back_current(tmp_path: Path) -> None:
    _, raw, command = _workspace(tmp_path)

    first = command(_document())
    second = command(
        _document(
            revision_id=REVISION_2,
            explanation="第二版白話內容。",
        )
    )
    stale_replay = command(_document())

    assert stale_replay.document_id == first.document_id
    assert stale_replay.replayed is True
    assert stale_replay.is_current is False

    connection = raw.connect()
    try:
        current = connection.execute(
            "SELECT id,sequence_no FROM search_documents "
            "WHERE work_id=? AND projection_kind=? AND is_current=1",
            (WORK, "paper_reading"),
        ).fetchone()
        assert tuple(current) == (second.document_id, 2)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision_id", REVISION_2),
        ("projection_kind", "paper_abstract"),
        ("title", "Changed title"),
        ("abstract", "Changed abstract"),
        ("explanation", "不同的白話內容。"),
    ],
)
def test_search_document_identity_binds_every_semantic_input(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    _, _, command = _workspace(tmp_path)

    first = command(_document())
    changed = command(replace(_document(), **{field: value}))

    assert changed.document_id != first.document_id
    assert changed.input_fingerprint != first.input_fingerprint
    assert changed.text_object_id != first.text_object_id


def test_missing_revision_fails_before_artifact_publication(tmp_path: Path) -> None:
    _, raw, command = _workspace(tmp_path)
    missing = "revision:" + "9" * 64

    with pytest.raises(SearchDocumentError, match="search_document_revision_missing"):
        command(_document(revision_id=missing))

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM search_documents"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM object_registry "
            "WHERE retention_policy='search-document-v1'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "change",
    [
        {"work_id": "bad"},
        {"revision_id": "bad"},
        {"projection_kind": "Paper Reading"},
        {"title": ""},
        {"title": "   "},
        {"abstract": "\x00"},
        {"explanation": "\ud800"},
    ],
)
def test_invalid_document_input_fails_without_side_effects(
    tmp_path: Path,
    change: dict[str, str],
) -> None:
    _, raw, command = _workspace(tmp_path)

    with pytest.raises(SearchDocumentError, match="invalid_search_document"):
        command(_document(**change))

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM search_documents"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM object_registry "
            "WHERE retention_policy='search-document-v1'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_registered_search_artifact_must_be_available_and_exact(
    tmp_path: Path,
) -> None:
    _, raw, command = _workspace(tmp_path)
    first = command(_document())

    connection = raw.connect()
    try:
        connection.execute(
            "UPDATE object_registry SET state='quarantined' WHERE object_id=?",
            (first.text_object_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception):
        command(_document())
