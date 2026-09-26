from dataclasses import replace
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
from libs.retrieval.adapters.driven.filesystem_active_index_artifact_reader_adapter import (
    FilesystemActiveIndexArtifactReaderAdapter,
)
from libs.retrieval.adapters.driven.filesystem_active_index_artifact_repair_adapter import (
    FilesystemActiveIndexArtifactRepairAdapter,
)
from libs.retrieval.adapters.driven.kernel_index_vector_loader_adapter import (
    KernelIndexVectorLoaderAdapter,
)
from libs.retrieval.application.commands.rebuild_active_index_artifacts import (
    RebuildActiveIndexArtifacts,
)
from libs.retrieval.dtos.active_index import ActivateIndexInput
from libs.retrieval.dtos.index_build import BuiltIndex
from libs.retrieval.exceptions.active_index_error import ActiveIndexError
from libs.retrieval.tests.integration.test_active_index_generation import (
    _active_store,
)
from libs.retrieval.tests.integration.test_build_index_generation import (
    FixtureBuilder,
    NOW,
    _command,
)


class DifferentBuilder:
    def build(self, request):
        return BuiltIndex(
            request.generation_id,
            len(request.vectors),
            b"different-index-bytes",
        )


def _fixture(tmp_path: Path):
    root, raw, store, config, build = _command(
        tmp_path,
        FixtureBuilder(),
        clock=lambda: NOW,
    )
    generation = build(config)
    active = _active_store(SqliteConnectionFactory(root))
    pin = active.activate(
        ActivateIndexInput(
            generation.space_id,
            generation.generation_id,
            None,
            None,
        ),
        activated_at=NOW,
    )
    reader = ReadObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(
            SqliteConnectionFactory(root)
        ),
    )
    return root, raw, store, config, pin, reader


def _repair(root, store, reader, builder):
    return RebuildActiveIndexArtifacts(
        store,
        KernelIndexVectorLoaderAdapter(reader),
        builder,
        FilesystemActiveIndexArtifactRepairAdapter(root),
        FilesystemActiveIndexArtifactReaderAdapter(root),
    )


def _rows(raw):
    connection = raw.connect()
    try:
        generation = tuple(
            connection.execute(
                "SELECT * FROM index_generations"
            ).fetchone()
        )
        active = tuple(
            connection.execute(
                "SELECT * FROM active_indexes"
            ).fetchone()
        )
    finally:
        connection.close()
    return generation, active


def test_missing_artifacts_rebuild_historical_active_generation(
    tmp_path: Path,
) -> None:
    root, raw, store, config, pin, reader = _fixture(tmp_path)
    before = _rows(raw)
    connection = raw.connect()
    try:
        first = connection.execute(
            "SELECT id FROM search_documents "
            "WHERE is_current=1 ORDER BY sequence_no LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "UPDATE search_documents SET is_current=0 WHERE id=?",
            (first,),
        )
        connection.commit()
    finally:
        connection.close()

    directory = root / pin.relative_directory
    (directory / "index.faiss").unlink()
    (directory / "manifest.json").unlink()

    result = _repair(
        root,
        store,
        reader,
        FixtureBuilder(),
    )(pin, config)

    assert result.pin == pin
    assert result.index_bytes
    assert (directory / "index.faiss").is_file()
    assert (directory / "manifest.json").is_file()
    assert _rows(raw) == before


@pytest.mark.parametrize("name", ["index.faiss", "manifest.json"])
def test_corrupt_derived_file_is_explicitly_repaired(
    tmp_path: Path,
    name: str,
) -> None:
    root, raw, store, config, pin, reader = _fixture(tmp_path)
    before = _rows(raw)
    target = root / pin.relative_directory / name
    target.write_bytes(target.read_bytes() + b"corrupt")

    result = _repair(
        root,
        store,
        reader,
        FixtureBuilder(),
    )(pin, config)

    assert result.pin == pin
    assert _rows(raw) == before
    assert FilesystemActiveIndexArtifactReaderAdapter(root).read(pin) == result


def test_rebuild_config_must_reproduce_same_generation_identity(
    tmp_path: Path,
) -> None:
    root, raw, store, config, pin, reader = _fixture(tmp_path)
    before = _rows(raw)
    directory = root / pin.relative_directory
    (directory / "index.faiss").unlink()
    (directory / "manifest.json").unlink()
    changed = replace(config, faiss_version="different-runtime")

    with pytest.raises(
        ActiveIndexError,
        match="active_index_rebuild_config_mismatch",
    ):
        _repair(
            root,
            store,
            reader,
            FixtureBuilder(),
        )(pin, changed)

    assert _rows(raw) == before
    assert not (directory / "index.faiss").exists()
    assert not (directory / "manifest.json").exists()


def test_builder_hash_mismatch_does_not_replace_derived_bytes(
    tmp_path: Path,
) -> None:
    root, raw, store, config, pin, reader = _fixture(tmp_path)
    before = _rows(raw)
    target = root / pin.relative_directory / "index.faiss"
    target.write_bytes(b"known-corrupt-index")
    corrupt = target.read_bytes()

    with pytest.raises(
        ActiveIndexError,
        match="active_index_rebuild_hash_mismatch",
    ):
        _repair(
            root,
            store,
            reader,
            DifferentBuilder(),
        )(pin, config)

    assert target.read_bytes() == corrupt
    assert _rows(raw) == before
