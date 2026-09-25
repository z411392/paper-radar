import math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import (
    FilesystemObjectBytesAdapter,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import (
    SqliteConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.kernel.application.commands.publish_object import PublishObject
from libs.retrieval.adapters.driven.kernel_embedding_batch_artifact_adapter import (
    KernelEmbeddingBatchArtifactAdapter,
)
from libs.retrieval.adapters.driven.sqlite_embedding_space_store_adapter import (
    SqliteEmbeddingSpaceStoreAdapter,
)
from libs.retrieval.adapters.driven.sqlite_embedding_store_adapter import (
    SqliteEmbeddingStoreAdapter,
)
from libs.retrieval.application.commands.persist_embedding_batch import (
    PersistEmbeddingBatch,
)
from libs.retrieval.application.commands.register_embedding_space import (
    RegisterEmbeddingSpace,
)
from libs.retrieval.domain.services.npy_float32_batch_codec import (
    NpyFloat32BatchCodec,
)
from libs.retrieval.dtos.embedding_batch import (
    EmbeddingBatchInput,
    EmbeddingVectorEntry,
)
from libs.retrieval.dtos.embedding_space import EmbeddingSpaceInput
from libs.retrieval.exceptions.embedding_batch_error import EmbeddingBatchError
from libs.retrieval.tests.integration.test_search_document_persistence import (
    REVISION_2,
    _document,
    _workspace,
)


NOW = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)


def _space_input(dimension: int = 4) -> EmbeddingSpaceInput:
    return EmbeddingSpaceInput(
        provider="fixture",
        model_name="embedding-model",
        model_revision="rev-2026-09",
        dimension=dimension,
        dtype="float32",
        normalization_version="l2-v1",
        prefix_config_hash="a" * 64,
        metric="inner_product",
    )


def _commands(tmp_path: Path):
    root, raw, prepare_document = _workspace(tmp_path)
    migrations = load_workspace_migrations(with_runtime=True)
    schema = SqliteSchemaConnectionFactory(
        root,
        migrations,
        minimum_version=25,
    )
    spaces = SqliteEmbeddingSpaceStoreAdapter(schema.connect)
    register = RegisterEmbeddingSpace(spaces, clock=lambda: NOW)
    artifact = KernelEmbeddingBatchArtifactAdapter(
        PublishObject(
            FilesystemObjectBytesAdapter(root),
            SqliteObjectUnitOfWorkAdapter(SqliteConnectionFactory(root)),
        )
    )
    persist = PersistEmbeddingBatch(
        spaces,
        artifact,
        SqliteEmbeddingStoreAdapter(schema.connect),
        clock=lambda: NOW,
    )
    return root, raw, prepare_document, register, persist


def _documents(prepare):
    first = prepare(
        _document(
            projection_kind="paper_abstract",
            title="Embedding alpha",
        )
    )
    second = prepare(
        _document(
            revision_id=REVISION_2,
            projection_kind="paper_explanation",
            title="Embedding beta",
        )
    )
    return first, second


def _entry(document_id: str, values) -> EmbeddingVectorEntry:
    return EmbeddingVectorEntry(document_id, tuple(values))


def test_batch_persists_npy_object_and_stable_integer_ids(tmp_path: Path) -> None:
    root, raw, prepare, register, persist = _commands(tmp_path)
    space = register(_space_input())
    first, second = _documents(prepare)

    result = persist(
        EmbeddingBatchInput(
            space.space_id,
            (
                _entry(second.document_id, (0.0, 1.0, 0.0, 0.0)),
                _entry(first.document_id, (1.0, 0.0, 0.0, 0.0)),
            ),
        )
    )

    assert result.object_id.startswith("embedding:")
    assert [item.document_id for item in result.embeddings] == sorted(
        [first.document_id, second.document_id]
    )
    assert [item.row_offset for item in result.embeddings] == [0, 1]
    assert all(type(item.embedding_id) is int and item.embedding_id > 0 for item in result.embeddings)
    assert all(item.replayed is False for item in result.embeddings)

    connection = raw.connect()
    try:
        obj = connection.execute(
            "SELECT * FROM object_registry WHERE object_id=?",
            (result.object_id,),
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM embeddings ORDER BY row_offset"
        ).fetchall()
    finally:
        connection.close()
    assert obj["kind"] == "embedding"
    assert obj["media_type"] == "application/x-npy"
    assert obj["retention_policy"] == "embedding-vector-batch-v1"
    assert len(rows) == 2

    content = (root / obj["relative_path"]).read_bytes()
    assert content.startswith(b"\x93NUMPY\x01\x00")
    decoded = NpyFloat32BatchCodec.decode(content)
    assert len(decoded) == 2
    assert all(len(row) == 4 for row in decoded)


def test_reordered_exact_replay_keeps_object_offsets_and_embedding_ids(
    tmp_path: Path,
) -> None:
    _, _, prepare, register, persist = _commands(tmp_path)
    space = register(_space_input())
    first, second = _documents(prepare)

    left = persist(
        EmbeddingBatchInput(
            space.space_id,
            (
                _entry(second.document_id, (0.0, 1.0, 0.0, 0.0)),
                _entry(first.document_id, (1.0, 0.0, 0.0, 0.0)),
            ),
        )
    )
    right = persist(
        EmbeddingBatchInput(
            space.space_id,
            (
                _entry(first.document_id, (1.0, 0.0, 0.0, 0.0)),
                _entry(second.document_id, (0.0, 1.0, 0.0, 0.0)),
            ),
        )
    )

    assert right.object_id == left.object_id
    assert [item.embedding_id for item in right.embeddings] == [
        item.embedding_id for item in left.embeddings
    ]
    assert [item.row_offset for item in right.embeddings] == [0, 1]
    assert all(item.replayed is True for item in right.embeddings)


@pytest.mark.parametrize(
    "entries",
    [
        (),
        (_entry("searchdoc:" + "a" * 64, (0.0, 0.0, 0.0, 0.0)),),
        (_entry("searchdoc:" + "a" * 64, (1.0, 2.0, 3.0)),),
        (_entry("searchdoc:" + "a" * 64, (1.0, math.nan, 0.0, 0.0)),),
        (_entry("searchdoc:" + "a" * 64, (1.0, math.inf, 0.0, 0.0)),),
        (_entry("searchdoc:" + "a" * 64, (1e100, 1.0, 0.0, 0.0)),),
        (
            _entry("searchdoc:" + "a" * 64, (1.0, 0.0, 0.0, 0.0)),
            _entry("searchdoc:" + "a" * 64, (0.0, 1.0, 0.0, 0.0)),
        ),
    ],
)
def test_invalid_vectors_fail_before_object_publication(
    tmp_path: Path,
    entries,
) -> None:
    _, raw, _, register, persist = _commands(tmp_path)
    space = register(_space_input())

    with pytest.raises(EmbeddingBatchError):
        persist(EmbeddingBatchInput(space.space_id, entries))

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM embeddings"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM object_registry WHERE kind='embedding'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_vector_for_missing_document_fails_without_publication(tmp_path: Path) -> None:
    _, raw, _, register, persist = _commands(tmp_path)
    space = register(_space_input())

    with pytest.raises(EmbeddingBatchError, match="embedding_document_missing"):
        persist(
            EmbeddingBatchInput(
                space.space_id,
                (_entry("searchdoc:" + "f" * 64, (1.0, 0.0, 0.0, 0.0)),),
            )
        )

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM object_registry WHERE kind='embedding'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_existing_embedding_row_conflict_fails_closed(tmp_path: Path) -> None:
    _, raw, prepare, register, persist = _commands(tmp_path)
    space = register(_space_input())
    first, _ = _documents(prepare)
    result = persist(
        EmbeddingBatchInput(
            space.space_id,
            (_entry(first.document_id, (1.0, 0.0, 0.0, 0.0)),),
        )
    )

    connection = raw.connect()
    try:
        connection.execute(
            "UPDATE embeddings SET row_offset=9 WHERE id=?",
            (result.embeddings[0].embedding_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(EmbeddingBatchError, match="embedding_row_conflict"):
        persist(
            EmbeddingBatchInput(
                space.space_id,
                (_entry(first.document_id, (1.0, 0.0, 0.0, 0.0)),),
            )
        )


def test_naive_clock_is_rejected_before_publication(tmp_path: Path) -> None:
    root, raw, prepare_document = _workspace(tmp_path)
    migrations = load_workspace_migrations(with_runtime=True)
    schema = SqliteSchemaConnectionFactory(root, migrations, minimum_version=25)
    spaces = SqliteEmbeddingSpaceStoreAdapter(schema.connect)
    space = RegisterEmbeddingSpace(spaces, clock=lambda: NOW)(_space_input())
    document = prepare_document(_document())
    artifact = KernelEmbeddingBatchArtifactAdapter(
        PublishObject(
            FilesystemObjectBytesAdapter(root),
            SqliteObjectUnitOfWorkAdapter(SqliteConnectionFactory(root)),
        )
    )
    persist = PersistEmbeddingBatch(
        spaces,
        artifact,
        SqliteEmbeddingStoreAdapter(schema.connect),
        clock=lambda: datetime(2026, 9, 26),
    )

    with pytest.raises(EmbeddingBatchError, match="embedding_batch_time"):
        persist(
            EmbeddingBatchInput(
                space.space_id,
                (_entry(document.document_id, (1.0, 0.0, 0.0, 0.0)),),
            )
        )

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM object_registry WHERE kind='embedding'"
        ).fetchone()[0] == 0
    finally:
        connection.close()
