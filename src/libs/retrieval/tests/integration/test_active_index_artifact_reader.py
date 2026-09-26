import json
from pathlib import Path

import pytest

from libs.retrieval.adapters.driven.filesystem_active_index_artifact_reader_adapter import (
    FilesystemActiveIndexArtifactReaderAdapter,
)
from libs.retrieval.exceptions.active_index_error import ActiveIndexError
from libs.retrieval.tests.integration.test_active_index_generation import (
    NOW,
    _active_store,
)
from libs.retrieval.tests.integration.test_build_index_generation import (
    FixtureBuilder,
    _command,
)
from libs.retrieval.dtos.active_index import ActivateIndexInput


def _active(tmp_path: Path):
    root, _, _, config, build = _command(
        tmp_path,
        FixtureBuilder(),
        clock=lambda: NOW,
    )
    generation = build(config)
    store = _active_store(
        __import__(
            "libs.kernel.adapters.driven.sqlite_connection_factory",
            fromlist=["SqliteConnectionFactory"],
        ).SqliteConnectionFactory(root)
    )
    pin = store.activate(
        ActivateIndexInput(
            generation.space_id,
            generation.generation_id,
            None,
            None,
        ),
        activated_at=NOW,
    )
    return root, pin


def test_reader_verifies_index_and_canonical_manifest(tmp_path: Path) -> None:
    root, pin = _active(tmp_path)

    result = FilesystemActiveIndexArtifactReaderAdapter(root).read(pin)

    assert result.pin == pin
    assert result.index_bytes
    data = json.loads(result.manifest_bytes)
    assert data["generation_id"] == pin.generation_id
    assert data["space_id"] == pin.space_id
    assert data["index_sha256"] == pin.index_sha256
    assert data["membership_digest"] == pin.membership_digest
    assert len(data["members"]) == pin.vector_count
    assert result.embedding_ids == tuple(
        item["embedding_id"] for item in data["members"]
    )


@pytest.mark.parametrize("name", ["index.faiss", "manifest.json"])
def test_missing_active_artifact_is_explicit_error(
    tmp_path: Path,
    name: str,
) -> None:
    root, pin = _active(tmp_path)
    (root / pin.relative_directory / name).unlink()

    with pytest.raises(ActiveIndexError, match="active_index_artifact_missing"):
        FilesystemActiveIndexArtifactReaderAdapter(root).read(pin)


@pytest.mark.parametrize("name", ["index.faiss", "manifest.json"])
def test_corrupt_active_artifact_is_rejected(
    tmp_path: Path,
    name: str,
) -> None:
    root, pin = _active(tmp_path)
    target = root / pin.relative_directory / name
    target.write_bytes(target.read_bytes() + b"x")

    with pytest.raises(ActiveIndexError, match="active_index_artifact_corrupt"):
        FilesystemActiveIndexArtifactReaderAdapter(root).read(pin)


def test_manifest_membership_digest_is_recomputed_not_trusted(
    tmp_path: Path,
) -> None:
    root, pin = _active(tmp_path)
    target = root / pin.relative_directory / "manifest.json"
    data = json.loads(target.read_bytes())
    data["members"][0]["row_offset"] += 1
    content = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    target.write_bytes(content)

    damaged_pin = pin.__class__(
        pin.space_id,
        pin.generation_id,
        pin.pointer_version,
        pin.relative_directory,
        pin.dimension,
        pin.dtype,
        pin.metric,
        pin.index_sha256,
        __import__("hashlib").sha256(content).hexdigest(),
        pin.membership_digest,
        pin.document_high_watermark,
        pin.vector_count,
        pin.activated_at,
        pin.replayed,
    )

    with pytest.raises(ActiveIndexError, match="active_index_manifest_mismatch"):
        FilesystemActiveIndexArtifactReaderAdapter(root).read(damaged_pin)


def test_noncanonical_manifest_bytes_are_rejected(tmp_path: Path) -> None:
    root, pin = _active(tmp_path)
    target = root / pin.relative_directory / "manifest.json"
    data = json.loads(target.read_bytes())
    target.write_bytes(json.dumps(data, indent=2).encode("ascii"))

    damaged_pin = pin.__class__(
        pin.space_id,
        pin.generation_id,
        pin.pointer_version,
        pin.relative_directory,
        pin.dimension,
        pin.dtype,
        pin.metric,
        pin.index_sha256,
        __import__("hashlib").sha256(target.read_bytes()).hexdigest(),
        pin.membership_digest,
        pin.document_high_watermark,
        pin.vector_count,
        pin.activated_at,
        pin.replayed,
    )

    with pytest.raises(ActiveIndexError, match="active_index_manifest_mismatch"):
        FilesystemActiveIndexArtifactReaderAdapter(root).read(damaged_pin)
