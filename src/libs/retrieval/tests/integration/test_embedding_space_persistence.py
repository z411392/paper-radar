from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.bundled_workspace_migrations import (
    load_workspace_migrations,
)
from libs.kernel.adapters.driven.sqlite_schema_connection_factory import (
    SqliteSchemaConnectionFactory,
)
from libs.retrieval.adapters.driven.sqlite_embedding_space_store_adapter import (
    SqliteEmbeddingSpaceStoreAdapter,
)
from libs.retrieval.application.commands.register_embedding_space import (
    RegisterEmbeddingSpace,
)
from libs.retrieval.dtos.embedding_space import EmbeddingSpaceInput
from libs.retrieval.exceptions.embedding_space_error import EmbeddingSpaceError
from libs.retrieval.tests.integration.test_search_document_persistence import (
    _workspace,
)


NOW = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)


def _input(**changes):
    value = EmbeddingSpaceInput(
        provider="fixture",
        model_name="embedding-model",
        model_revision="rev-2026-09",
        dimension=4,
        dtype="float32",
        normalization_version="l2-v1",
        prefix_config_hash="a" * 64,
        metric="inner_product",
    )
    return replace(value, **changes)


def _register(tmp_path: Path):
    root, raw, _ = _workspace(tmp_path)
    schema = SqliteSchemaConnectionFactory(
        root,
        load_workspace_migrations(with_runtime=True),
        minimum_version=25,
    )
    command = RegisterEmbeddingSpace(
        SqliteEmbeddingSpaceStoreAdapter(schema.connect),
        clock=lambda: NOW,
    )
    return root, raw, command


def test_exact_space_replay_keeps_identity_and_created_at(tmp_path: Path) -> None:
    _, raw, register = _register(tmp_path)

    first = register(_input())
    second = register(_input())

    assert first.space_id.startswith("embspace:")
    assert len(first.configuration_fingerprint) == 64
    assert first.created_at == NOW
    assert first.replayed is False
    assert second.space_id == first.space_id
    assert second.configuration_fingerprint == first.configuration_fingerprint
    assert second.created_at == first.created_at
    assert second.replayed is True

    connection = raw.connect()
    try:
        rows = connection.execute("SELECT * FROM embedding_spaces").fetchall()
    finally:
        connection.close()
    assert len(rows) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_revision", "rev-2026-10"),
        ("dimension", 8),
        ("normalization_version", "none-v1"),
        ("prefix_config_hash", "b" * 64),
        ("metric", "l2"),
    ],
)
def test_configuration_change_creates_distinct_space(
    tmp_path: Path,
    field: str,
    value,
) -> None:
    _, raw, register = _register(tmp_path)

    first = register(_input())
    second = register(_input(**{field: value}))

    assert second.space_id != first.space_id
    assert second.configuration_fingerprint != first.configuration_fingerprint

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM embedding_spaces"
        ).fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.parametrize(
    "value",
    [
        _input(dimension=0),
        _input(dimension=True),
        _input(dtype="float64"),
        _input(metric="cosine"),
        _input(prefix_config_hash="short"),
        _input(provider=""),
        _input(model_name=" "),
        _input(model_revision="bad\0revision"),
    ],
)
def test_invalid_space_fails_without_persistence(
    tmp_path: Path,
    value: EmbeddingSpaceInput,
) -> None:
    _, raw, register = _register(tmp_path)

    with pytest.raises(EmbeddingSpaceError):
        register(value)

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM embedding_spaces"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_existing_space_row_conflict_fails_closed(tmp_path: Path) -> None:
    _, raw, register = _register(tmp_path)
    first = register(_input())

    connection = raw.connect()
    try:
        connection.execute(
            "UPDATE embedding_spaces SET provider='tampered' WHERE id=?",
            (first.space_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        EmbeddingSpaceError,
        match="embedding_space_conflict",
    ):
        register(_input())


def test_naive_clock_is_rejected_without_persistence(tmp_path: Path) -> None:
    root, raw, _ = _workspace(tmp_path)
    schema = SqliteSchemaConnectionFactory(
        root,
        load_workspace_migrations(with_runtime=True),
        minimum_version=25,
    )
    command = RegisterEmbeddingSpace(
        SqliteEmbeddingSpaceStoreAdapter(schema.connect),
        clock=lambda: datetime(2026, 9, 26),
    )

    with pytest.raises(EmbeddingSpaceError, match="embedding_space_time"):
        command(_input())

    connection = raw.connect()
    try:
        assert connection.execute(
            "SELECT count(*) FROM embedding_spaces"
        ).fetchone()[0] == 0
    finally:
        connection.close()
