import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.retrieval.adapters.driven.sqlite_index_generation_store_adapter import (
    SqliteIndexGenerationStoreAdapter,
)
from libs.retrieval.domain.services.index_generation_rules import IndexGenerationRules
from libs.retrieval.dtos.index_generation import IndexGenerationInput
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.tests.integration.test_embedding_batch_persistence import (
    _commands,
    _documents,
    _entry,
    _space_input,
)
from libs.retrieval.dtos.embedding_batch import EmbeddingBatchInput


NOW = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)


def _generation(tmp_path: Path):
    root, raw, prepare, register, persist = _commands(tmp_path)
    space = register(_space_input())
    first, second = _documents(prepare)
    persisted = persist(
        EmbeddingBatchInput(
            space.space_id,
            (
                _entry(first.document_id, (1.0, 0.0, 0.0, 0.0)),
                _entry(second.document_id, (0.0, 1.0, 0.0, 0.0)),
            ),
        )
    )
    schema = SqliteSchemaConnectionFactory(
        root,
        load_workspace_migrations(with_runtime=True),
        minimum_version=25,
    )
    store = SqliteIndexGenerationStoreAdapter(schema.connect)
    config = IndexGenerationInput(
        space.space_id,
        "flat-idmap-v1",
        "faiss-flat-v1",
        "1.15.1",
    )
    return root, raw, prepare, space, persisted, store, config


def test_snapshot_uses_stable_embedding_ids_and_is_deterministic(
    tmp_path: Path,
) -> None:
    _, _, _, space, persisted, store, config = _generation(tmp_path)

    snapshot = store.snapshot(space.space_id)
    prepared = IndexGenerationRules.prepare(snapshot, config)
    again = IndexGenerationRules.prepare(snapshot, config)

    expected_ids = tuple(
        sorted(item.embedding_id for item in persisted.embeddings)
    )
    assert tuple(item.embedding_id for item in snapshot.members) == expected_ids
    assert snapshot.vector_count == 2
    assert snapshot.dimension == 4
    assert snapshot.metric == "inner_product"
    assert snapshot.document_high_watermark >= 2
    assert prepared == again
    assert prepared.generation_id.startswith("faissgen:")
    assert prepared.relative_directory.startswith("derived/faiss/")
    assert prepared.vector_count == len(prepared.members) == 2


def test_generation_manifest_contains_complete_stable_mapping(
    tmp_path: Path,
) -> None:
    _, _, _, space, persisted, store, config = _generation(tmp_path)
    prepared = IndexGenerationRules.prepare(
        store.snapshot(space.space_id),
        config,
    )

    manifest = IndexGenerationRules.manifest(
        prepared,
        index_sha256="d" * 64,
    )
    data = json.loads(manifest.content_bytes)

    assert manifest.manifest_sha256 == __import__("hashlib").sha256(
        manifest.content_bytes
    ).hexdigest()
    assert data["generation_id"] == prepared.generation_id
    assert data["space_id"] == space.space_id
    assert data["index_sha256"] == "d" * 64
    assert data["membership_digest"] == prepared.membership_digest
    assert data["vector_count"] == 2
    assert [item["embedding_id"] for item in data["members"]] == [
        item.embedding_id for item in prepared.members
    ]
    assert [item["embedding_id"] for item in data["members"]] == [
        item.embedding_id for item in persisted.embeddings
    ]


def test_generation_start_persists_complete_membership_and_exact_replay(
    tmp_path: Path,
) -> None:
    _, raw, _, space, _, store, config = _generation(tmp_path)
    prepared = IndexGenerationRules.prepare(
        store.snapshot(space.space_id),
        config,
    )

    first = store.start(prepared, created_at=NOW)
    replay = store.start(prepared, created_at=NOW)

    assert first.generation_id == replay.generation_id
    assert first.state == replay.state == "building"
    assert first.replayed is False
    assert replay.replayed is True

    connection = raw.connect()
    try:
        generation = connection.execute(
            "SELECT * FROM index_generations WHERE id=?",
            (prepared.generation_id,),
        ).fetchone()
        members = connection.execute(
            "SELECT embedding_id FROM index_generation_members "
            "WHERE generation_id=? ORDER BY embedding_id",
            (prepared.generation_id,),
        ).fetchall()
    finally:
        connection.close()
    assert generation["vector_count"] == len(prepared.members)
    assert generation["membership_digest"] == prepared.membership_digest
    assert [row["embedding_id"] for row in members] == [
        item.embedding_id for item in prepared.members
    ]


def test_missing_current_document_embedding_rejects_partial_generation(
    tmp_path: Path,
) -> None:
    _, _, prepare, space, _, store, _ = _generation(tmp_path)

    prepare(
        __import__(
            "libs.retrieval.tests.integration.test_search_document_persistence",
            fromlist=["_document"],
        )._document(
            work_id="work:3",
            revision_id="revision:3",
            projection_kind="paper_abstract",
            title="Missing vector",
        )
    )

    with pytest.raises(
        IndexGenerationError,
        match="embedding_coverage_incomplete",
    ):
        store.snapshot(space.space_id)


def test_duplicate_embedding_mapping_rejects_generation_snapshot(
    tmp_path: Path,
) -> None:
    _, raw, _, space, persisted, store, _ = _generation(tmp_path)
    first = persisted.embeddings[0]

    connection = raw.connect()
    try:
        connection.execute(
            "INSERT INTO embeddings("
            "document_id,space_id,object_id,row_offset,input_fingerprint,created_at"
            ") VALUES(?,?,?,?,?,?)",
            (
                first.document_id,
                first.space_id,
                first.object_id,
                first.row_offset,
                "f" * 64,
                NOW.isoformat(),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        IndexGenerationError,
        match="embedding_membership_conflict",
    ):
        store.snapshot(space.space_id)


def test_generation_identity_changes_with_faiss_runtime_version(
    tmp_path: Path,
) -> None:
    _, _, _, space, _, store, config = _generation(tmp_path)
    snapshot = store.snapshot(space.space_id)
    left = IndexGenerationRules.prepare(snapshot, config)
    right = IndexGenerationRules.prepare(
        snapshot,
        IndexGenerationInput(
            space.space_id,
            config.index_kind,
            config.builder_version,
            "1.15.2",
        ),
    )

    assert left.generation_id != right.generation_id
    assert left.membership_digest == right.membership_digest


@pytest.mark.parametrize(
    "field,value",
    [
        ("faiss_version", ""),
        ("index_kind", "ivf"),
        ("builder_version", ""),
    ],
)
def test_invalid_generation_configuration_is_rejected(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    _, _, _, space, _, store, config = _generation(tmp_path)
    snapshot = store.snapshot(space.space_id)
    changed = {
        "space_id": config.space_id,
        "index_kind": config.index_kind,
        "builder_version": config.builder_version,
        "faiss_version": config.faiss_version,
    }
    changed[field] = value

    with pytest.raises(IndexGenerationError):
        IndexGenerationRules.prepare(
            snapshot,
            IndexGenerationInput(**changed),
        )
