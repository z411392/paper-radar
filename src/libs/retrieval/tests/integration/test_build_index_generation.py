import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from libs.kernel.adapters.driven.filesystem_object_bytes_adapter import (
    FilesystemObjectBytesAdapter,
)
from libs.kernel.adapters.driven.sqlite_connection_factory import (
    SqliteConnectionFactory,
)
from libs.kernel.adapters.driven.sqlite_object_unit_of_work_adapter import (
    SqliteObjectUnitOfWorkAdapter,
)
from libs.kernel.application.queries.read_object import ReadObject
from libs.retrieval.adapters.driven.filesystem_index_generation_artifact_adapter import (
    FilesystemIndexGenerationArtifactAdapter,
)
from libs.retrieval.adapters.driven.kernel_index_vector_loader_adapter import (
    KernelIndexVectorLoaderAdapter,
)
from libs.retrieval.application.commands.build_index_generation import (
    BuildIndexGeneration,
)
from libs.retrieval.dtos.index_build import BuiltIndex
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.tests.integration.test_index_generation_manifest import (
    _generation,
)


NOW = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)


class FixtureBuilder:
    def __init__(self) -> None:
        self.calls = 0

    def build(self, request):
        self.calls += 1
        content = json.dumps(
            {
                "generation_id": request.generation_id,
                "dimension": request.dimension,
                "metric": request.metric,
                "ids": [item.embedding_id for item in request.vectors],
                "vectors": [item.values for item in request.vectors],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return BuiltIndex(
            request.generation_id,
            len(request.vectors),
            content,
        )


class FailingBuilder:
    def build(self, request):
        del request
        raise IndexGenerationError("fixture_builder_failed")


def _command(tmp_path: Path, builder, *, clock=lambda: NOW):
    root, raw, _, space, _, store, config = _generation(tmp_path)
    reader = ReadObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(
            SqliteConnectionFactory(root)
        ),
    )
    command = BuildIndexGeneration(
        store,
        KernelIndexVectorLoaderAdapter(reader),
        builder,
        FilesystemIndexGenerationArtifactAdapter(root),
        clock=clock,
    )
    return root, raw, store, config, command


def test_build_orchestration_publishes_derived_files_then_marks_ready(
    tmp_path: Path,
) -> None:
    builder = FixtureBuilder()
    root, raw, _, config, command = _command(tmp_path, builder)

    result = command(config)

    assert result.state == "ready"
    assert result.replayed is False
    assert builder.calls == 1
    connection = raw.connect()
    try:
        row = connection.execute(
            "SELECT * FROM index_generations WHERE id=?",
            (result.generation_id,),
        ).fetchone()
    finally:
        connection.close()
    directory = root / row["relative_directory"]
    assert (directory / "index.faiss").is_file()
    assert (directory / "manifest.json").is_file()
    assert row["index_sha256"]
    assert row["manifest_sha256"]
    assert row["verified_at"] == NOW.isoformat()


def test_build_exact_replay_keeps_original_verified_at(
    tmp_path: Path,
) -> None:
    builder = FixtureBuilder()
    times = iter((NOW, NOW, NOW + timedelta(hours=2), NOW + timedelta(hours=3)))
    root, raw, _, config, command = _command(
        tmp_path,
        builder,
        clock=lambda: next(times),
    )

    first = command(config)
    replay = command(config)

    assert first.state == replay.state == "ready"
    assert first.replayed is False
    assert replay.replayed is True
    connection = raw.connect()
    try:
        verified = connection.execute(
            "SELECT verified_at FROM index_generations WHERE id=?",
            (first.generation_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert verified == NOW.isoformat()
    assert (root / first.relative_directory / "index.faiss").is_file()


def test_builder_failure_leaves_generation_building_for_replay(
    tmp_path: Path,
) -> None:
    _, raw, _, config, command = _command(tmp_path, FailingBuilder())

    with pytest.raises(IndexGenerationError, match="fixture_builder_failed"):
        command(config)

    connection = raw.connect()
    try:
        row = connection.execute(
            "SELECT state,index_sha256,manifest_sha256,verified_at "
            "FROM index_generations"
        ).fetchone()
    finally:
        connection.close()
    assert tuple(row) == ("building", None, None, None)
