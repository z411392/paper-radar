import hashlib
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
from libs.retrieval.domain.services.index_generation_rules import (
    IndexGenerationRules,
)
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.tests.integration.test_index_generation_manifest import (
    _generation,
)


def _prepared(tmp_path: Path):
    root, _, _, space, persisted, store, config = _generation(tmp_path)
    generation = IndexGenerationRules.prepare(
        store.snapshot(space.space_id),
        config,
    )
    reader = ReadObject(
        FilesystemObjectBytesAdapter(root),
        SqliteObjectUnitOfWorkAdapter(
            SqliteConnectionFactory(root)
        ),
    )
    return root, generation, persisted, reader


def test_vector_loader_restores_stable_ids_from_immutable_npy(
    tmp_path: Path,
) -> None:
    _, generation, persisted, reader = _prepared(tmp_path)

    request = KernelIndexVectorLoaderAdapter(reader).load(generation)

    assert tuple(item.embedding_id for item in request.vectors) == tuple(
        member.embedding_id for member in generation.members
    )
    assert tuple(item.embedding_id for item in request.vectors) == tuple(
        item.embedding_id
        for item in sorted(
            persisted.embeddings,
            key=lambda item: item.embedding_id,
        )
    )
    assert all(len(item.values) == generation.dimension for item in request.vectors)


def test_vector_loader_rejects_out_of_range_row_mapping(
    tmp_path: Path,
) -> None:
    _, generation, _, reader = _prepared(tmp_path)
    first = generation.members[0]
    changed = generation.__class__(
        generation.generation_id,
        generation.space_id,
        generation.space_configuration_fingerprint,
        generation.dimension,
        generation.dtype,
        generation.metric,
        generation.index_kind,
        generation.builder_version,
        generation.faiss_version,
        generation.document_high_watermark,
        generation.vector_count,
        generation.membership_digest,
        generation.relative_directory,
        (
            first.__class__(
                first.embedding_id,
                first.document_id,
                first.object_id,
                999,
                first.input_fingerprint,
                first.document_sequence_no,
            ),
            *generation.members[1:],
        ),
    )

    with pytest.raises(IndexGenerationError):
        KernelIndexVectorLoaderAdapter(reader).load(changed)


def test_derived_artifacts_publish_exact_files_and_replay(
    tmp_path: Path,
) -> None:
    root, generation, _, _ = _prepared(tmp_path)
    adapter = FilesystemIndexGenerationArtifactAdapter(root)
    index_bytes = b"opaque-index-fixture"
    index_sha = hashlib.sha256(index_bytes).hexdigest()
    manifest = IndexGenerationRules.manifest(
        generation,
        index_sha256=index_sha,
    )

    first = adapter.publish(
        generation,
        index_bytes=index_bytes,
        manifest=manifest,
    )
    replay = adapter.publish(
        generation,
        index_bytes=index_bytes,
        manifest=manifest,
    )

    assert first == replay
    directory = root / generation.relative_directory
    assert (directory / "index.faiss").read_bytes() == index_bytes
    assert (directory / "manifest.json").read_bytes() == manifest.content_bytes
    assert first.index_sha256 == index_sha
    assert first.manifest_sha256 == manifest.manifest_sha256


def test_derived_artifact_conflict_fails_closed(tmp_path: Path) -> None:
    root, generation, _, _ = _prepared(tmp_path)
    adapter = FilesystemIndexGenerationArtifactAdapter(root)
    index_bytes = b"opaque-index-fixture"
    manifest = IndexGenerationRules.manifest(
        generation,
        index_sha256=hashlib.sha256(index_bytes).hexdigest(),
    )
    adapter.publish(
        generation,
        index_bytes=index_bytes,
        manifest=manifest,
    )
    target = root / generation.relative_directory / "index.faiss"
    target.write_bytes(b"conflict")

    with pytest.raises(IndexGenerationError, match="index_artifact_conflict"):
        adapter.publish(
            generation,
            index_bytes=index_bytes,
            manifest=manifest,
        )


def test_vector_loader_reads_shared_batch_once(tmp_path: Path) -> None:
    _, generation, _, reader = _prepared(tmp_path)

    class CountingReader:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def __call__(self, object_id):
            self.calls.append(object_id)
            return self.inner(object_id)

    counted = CountingReader(reader)
    request = KernelIndexVectorLoaderAdapter(counted).load(generation)

    assert len(request.vectors) == generation.vector_count
    assert counted.calls == sorted(set(counted.calls))
    assert counted.calls == sorted(
        {member.object_id for member in generation.members}
    )


def test_derived_publish_resumes_after_index_only_crash(
    tmp_path: Path,
) -> None:
    root, generation, _, _ = _prepared(tmp_path)
    adapter = FilesystemIndexGenerationArtifactAdapter(root)
    index_bytes = b"opaque-index-fixture"
    index_sha = hashlib.sha256(index_bytes).hexdigest()
    manifest = IndexGenerationRules.manifest(
        generation,
        index_sha256=index_sha,
    )
    directory = root / generation.relative_directory
    directory.mkdir(parents=True)
    (directory / "index.faiss").write_bytes(index_bytes)

    before = SqliteConnectionFactory(root).connect()
    try:
        count_before = before.execute(
            "SELECT count(*) FROM object_registry"
        ).fetchone()[0]
    finally:
        before.close()

    result = adapter.publish(
        generation,
        index_bytes=index_bytes,
        manifest=manifest,
    )

    after = SqliteConnectionFactory(root).connect()
    try:
        count_after = after.execute(
            "SELECT count(*) FROM object_registry"
        ).fetchone()[0]
    finally:
        after.close()
    assert result.index_sha256 == index_sha
    assert (directory / "manifest.json").read_bytes() == manifest.content_bytes
    assert count_after == count_before
