from datetime import datetime, timezone
from pathlib import Path

import pytest

from libs.retrieval.domain.services.index_generation_rules import (
    IndexGenerationRules,
)
from libs.retrieval.exceptions.index_generation_error import IndexGenerationError
from libs.retrieval.tests.integration.test_index_generation_manifest import (
    _generation,
)


NOW = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)


def _building(tmp_path: Path):
    _, raw, _, space, _, store, config = _generation(tmp_path)
    prepared = IndexGenerationRules.prepare(
        store.snapshot(space.space_id),
        config,
    )
    started = store.start(prepared, created_at=NOW)
    return raw, store, prepared, started


def test_ready_cas_persists_hashes_and_exact_replay(tmp_path: Path) -> None:
    raw, store, prepared, started = _building(tmp_path)
    manifest = IndexGenerationRules.manifest(
        prepared,
        index_sha256="a" * 64,
    )

    first = store.mark_ready(
        prepared,
        manifest,
        index_sha256="a" * 64,
        verified_at=NOW,
    )
    replay = store.mark_ready(
        prepared,
        manifest,
        index_sha256="a" * 64,
        verified_at=NOW,
    )

    assert started.state == "building"
    assert first.state == replay.state == "ready"
    assert first.replayed is False
    assert replay.replayed is True

    connection = raw.connect()
    try:
        row = connection.execute(
            "SELECT state,index_sha256,manifest_sha256,verified_at "
            "FROM index_generations WHERE id=?",
            (prepared.generation_id,),
        ).fetchone()
    finally:
        connection.close()
    assert tuple(row) == (
        "ready",
        "a" * 64,
        manifest.manifest_sha256,
        NOW.isoformat(),
    )


def test_ready_rejects_manifest_or_index_hash_conflict(tmp_path: Path) -> None:
    _, store, prepared, _ = _building(tmp_path)
    manifest = IndexGenerationRules.manifest(
        prepared,
        index_sha256="a" * 64,
    )
    store.mark_ready(
        prepared,
        manifest,
        index_sha256="a" * 64,
        verified_at=NOW,
    )

    changed = IndexGenerationRules.manifest(
        prepared,
        index_sha256="b" * 64,
    )
    with pytest.raises(IndexGenerationError, match="index_generation_conflict"):
        store.mark_ready(
            prepared,
            changed,
            index_sha256="b" * 64,
            verified_at=NOW,
        )


def test_ready_rechecks_current_membership_before_transition(
    tmp_path: Path,
) -> None:
    raw, store, prepared, _ = _building(tmp_path)
    manifest = IndexGenerationRules.manifest(
        prepared,
        index_sha256="a" * 64,
    )

    connection = raw.connect()
    try:
        connection.execute(
            "UPDATE search_documents SET is_current=0 "
            "WHERE id=(SELECT id FROM search_documents WHERE is_current=1 LIMIT 1)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        IndexGenerationError,
        match="index_generation_snapshot_changed",
    ):
        store.mark_ready(
            prepared,
            manifest,
            index_sha256="a" * 64,
            verified_at=NOW,
        )


def test_build_failure_is_append_safe_and_never_overwrites_ready(
    tmp_path: Path,
) -> None:
    _, store, prepared, _ = _building(tmp_path)

    failed = store.mark_failed(
        prepared,
        failed_at=NOW,
    )
    replay = store.mark_failed(
        prepared,
        failed_at=NOW,
    )

    assert failed.state == replay.state == "failed"
    assert failed.replayed is False
    assert replay.replayed is True

    with pytest.raises(IndexGenerationError, match="index_generation_conflict"):
        store.mark_ready(
            prepared,
            IndexGenerationRules.manifest(
                prepared,
                index_sha256="a" * 64,
            ),
            index_sha256="a" * 64,
            verified_at=NOW,
        )


def test_failed_generation_cannot_replace_ready_generation(
    tmp_path: Path,
) -> None:
    _, store, prepared, _ = _building(tmp_path)
    manifest = IndexGenerationRules.manifest(
        prepared,
        index_sha256="a" * 64,
    )
    store.mark_ready(
        prepared,
        manifest,
        index_sha256="a" * 64,
        verified_at=NOW,
    )

    with pytest.raises(IndexGenerationError, match="index_generation_conflict"):
        store.mark_failed(
            prepared,
            failed_at=NOW,
        )
